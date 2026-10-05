# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Adaptive AF-C v2: realistic units (subject distance in METERS) +
confidence-scaled prediction, and does low contrast improve or stay stuck?

Real units: scenario is defined in subject DISTANCE (metres). A thin-lens
mapping converts distance -> lens focus position (mm, what the motor moves):
    focus_mm(D) = f^2 / (D - f),  f = 55 mm, D in mm   (infinity -> 0 mm)
so 0.6 m -> 6.8 mm (near), 1 m -> 3.2 mm, 5 m -> 0.6 mm, inf -> 0 mm. The AF
physics (PDAF/CDAF) run in mm; plots show metres.

(b) Confidence-scaled prediction: the Kalman lead = predict_frames * confidence,
so low-confidence frames do NOT extrapolate noise. Tests whether that rescues
the low-contrast jitter or whether low contrast stays measurement-limited.

Run:  python scripts/run_adaptive_v2.py   ->  out/adaptive_v2.png
"""
from __future__ import annotations
import os, sys
from dataclasses import dataclass, field
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdaf_sim.scene import high_contrast, low_contrast
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone, cdaf_score
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.policy import Decision
from pdaf_sim.latency import LatencyBuffer

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)
FPS = 60; N = 210; N_ZONES = 14; MAX_DISP_PX = 48
LENS_LO, LENS_HI = 0.0, 7.0
STEPS_PER_MM = 300.0
MAX_STEP_MM = 10000.0 / STEPS_PER_MM / FPS
MOTOR_RESPONSE = 0.6
LATENCY = 3; UPDATE_EVERY = 2
AE_RESPONSE = 0.18


def focus_mm(D_m):                       # subject distance (m) -> lens focus pos (mm)
    D = max(D_m * 1000.0, F_MM + 1.0)
    return float(np.clip(F_MM ** 2 / (D - F_MM), LENS_LO, LENS_HI))
def dist_m(x_mm):                        # lens focus pos (mm) -> subject distance (m)
    x = max(x_mm, 1e-4)
    return (F_MM + F_MM ** 2 / x) / 1000.0


def subject_m(k):                        # realistic subject distance trajectory, METRES
    if k < 70:  return 3.0 - 1.8 * (k / 70.0)   # steady walk-in 3.0 m -> 1.2 m
    if k < 140: return 0.6                        # STEP very close (front object)
    return 8.0                                     # STEP far (background)
def brightness(k):
    if k < 90:  return 1.0
    if k < 120: return float(1.0 - 0.7 * (k - 90) / 30.0)
    if k < 160: return 0.3
    return float(min(1.0, 0.3 + 0.7 * (k - 160) / 30.0))


@dataclass
class FirmwareTimeFilter:
    tau_sweep: float = 0.08; sweep_step: float = 0.3
    _est: float | None = None; _dir: int = 1
    def step(self, d, c, lens):
        if c < self.tau_sweep:
            cmd = lens + self._dir * self.sweep_step
            if cmd > LENS_HI or cmd < LENS_LO: self._dir *= -1; cmd = lens + self._dir * self.sweep_step
            self._est = cmd; return Decision(lens_cmd=float(cmd), swept=True)
        z = lens - d; self._est = z if self._est is None else 0.65 * self._est + 0.35 * z
        return Decision(lens_cmd=float(self._est))
    def coast(self): return Decision(lens_cmd=float(self._est or 0.0))


@dataclass
class AdaptivePolicy:
    process_var: float = 0.5 * 15 / FPS; meas_var_base: float = 1.0
    predict_frames: int = LATENCY; step_thresh: float = 0.8; tau_sweep: float = 0.05
    sweep_step: float = 0.3; conf_scaled: bool = False      # (b): scale lead by confidence
    _x: np.ndarray = field(default_factory=lambda: np.array([3.0, 0.0]))
    _P: np.ndarray = field(default_factory=lambda: np.eye(2) * 10.0); _dir: int = 1
    def step(self, d, c, lens):
        F = np.array([[1.0, 1.0], [0.0, 1.0]]); Q = np.eye(2) * self.process_var
        xp = F @ self._x; Pp = F @ self._P @ F.T + Q
        if c < self.tau_sweep:
            cmd = lens + self._dir * self.sweep_step
            if cmd > LENS_HI or cmd < LENS_LO: self._dir *= -1; cmd = lens + self._dir * self.sweep_step
            self._x = xp; self._P = Pp; return Decision(lens_cmd=float(cmd), swept=True)
        z = lens - d; innov = z - xp[0]
        if abs(innov) > self.step_thresh:
            self._x = np.array([z, 0.0]); self._P = np.eye(2) * 10.0
            return Decision(lens_cmd=float(z))
        H = np.array([[1.0, 0.0]]); R = np.array([[self.meas_var_base / max(c, 1e-3)]])
        K = Pp @ H.T @ np.linalg.inv(H @ Pp @ H.T + R)
        self._x = xp + (K @ np.array([innov])).flatten(); self._P = (np.eye(2) - K @ H) @ Pp
        lead = self.predict_frames * (c if self.conf_scaled else 1.0)   # (b)
        return Decision(lens_cmd=float(self._x[0] + self._x[1] * lead))
    def coast(self):
        F = np.array([[1.0, 1.0], [0.0, 1.0]]); self._x = F @ self._x
        lead = self.predict_frames
        return Decision(lens_cmd=float(self._x[0] + self._x[1] * lead))


def measure(sharp, cdaf_ref, lens, tgt_mm, level, nrng):
    L, R = render_lr(sharp, lens - tgt_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                     noise_sigma=0.006 + 0.030 * max(0.0, 1.0 - level), bin_factor=1, rng=nrng)
    disp, pconf = estimate_disparity_multi_zone(L, R, max_disp_px=MAX_DISP_PX, n_zones=N_ZONES)
    disp_mm = disp / PX_PER_MM
    sharp_ratio = float(np.clip(cdaf_score((L + R) * 0.5) / (cdaf_ref + 1e-9), 0, 1))
    fused = pconf + (0.35 * sharp_ratio if abs(disp_mm) < 0.5 else 0.0)
    return disp_mm, float(np.clip(fused, 0, 1))


def run(scene_fn, make_policy, seed=1):
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed + 7)
    sharp = scene_fn(rng); cdaf_ref = cdaf_score(sharp)
    pol = make_policy(); lb = LatencyBuffer(LATENCY)
    lens = focus_mm(subject_m(0)); last = lens; ae = 0.5 / brightness(0)
    H = dict(lens_mm=[], tgt_mm=[], subj_m=[], lens_m=[], err_mm=[])
    for k in range(N):
        tgt_mm = focus_mm(subject_m(k)); br = brightness(k)
        ae += AE_RESPONSE * (0.5 / max(br, 1e-3) - ae); level = br * ae
        if k % UPDATE_EVERY == 0:
            d, c = measure(sharp, cdaf_ref, lens, tgt_mm, level, nrng)
            arr = lb.push_pop((d, c, lens))
            if arr is not None: last = pol.step(arr[0], arr[1], arr[2]).lens_cmd
            elif hasattr(pol, "coast"): last = pol.coast().lens_cmd
        st = float(np.clip(last - lens, -MAX_STEP_MM, MAX_STEP_MM))
        lens = float(np.clip(lens + MOTOR_RESPONSE * st, LENS_LO, LENS_HI))
        H["lens_mm"].append(lens); H["tgt_mm"].append(tgt_mm)
        H["subj_m"].append(subject_m(k)); H["lens_m"].append(dist_m(lens))
        H["err_mm"].append(abs(lens - tgt_mm))
    return {kk: np.array(vv) for kk, vv in H.items()}


POL = [("firmware time-filter", FirmwareTimeFilter, "#BA7517"),
       ("Adaptive (fixed lead)", lambda: AdaptivePolicy(conf_scaled=False), "#378ADD"),
       ("Adaptive (conf-scaled lead)", lambda: AdaptivePolicy(conf_scaled=True), "#1D9E75")]
SCENES = [("high contrast", high_contrast), ("low contrast", low_contrast)]


def main():
    os.makedirs("out", exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(15, 9)); k = np.arange(N)
    print(f"{'scene':<14}{'policy':<30}{'mean defocus err(mm)':>22}")
    for row, (sname, sfn) in enumerate(SCENES):
        axT, axE = axes[row]
        axT.plot(k, [subject_m(i) for i in k], color="k", ls="--", lw=1.5, label="subject distance (m)")
        axb = axT.twinx(); axb.plot(k, [brightness(i) for i in k], color="#cfcfcf", lw=1.4, label="brightness")
        axb.set_ylabel("brightness", color="#999"); axb.set_ylim(0, 1.25)
        for name, mk, col in POL:
            r = run(sfn, mk)
            axT.plot(k, np.clip(r["lens_m"], 0, 12), color=col, lw=1.5, label=name)
            axE.plot(k, r["err_mm"], color=col, lw=1.4, label=name)
            print(f"{sname:<14}{name:<30}{r['err_mm'].mean():>22.3f}")
        axT.set_yscale("log"); axT.set_ylim(0.4, 12)
        axT.set_ylabel("distance (m)  [log]"); axT.set_title(f"{sname}: subject vs lens, real distance (m)", fontsize=9)
        h1, l1 = axT.get_legend_handles_labels(); h2, l2 = axb.get_legend_handles_labels()
        axT.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper right")
        axE.axhline(0.08, color="#aaa", ls=":"); axE.set_yscale("symlog", linthresh=0.08)
        axE.set_ylabel("defocus err (mm)"); axE.set_title(f"{sname}: focus error (defocus, mm)", fontsize=9)
        axE.legend(fontsize=7)
        if row == 1: axT.set_xlabel("frame (60fps)"); axE.set_xlabel("frame (60fps)")
    fig.suptitle("AF-C v2: real units (subject in metres) + confidence-scaled prediction, high vs low contrast", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.97]); fig.savefig("out/adaptive_v2.png", dpi=120)
    print("\nsaved out/adaptive_v2.png")
    print(f"distance->lens check: 0.6m={focus_mm(0.6):.2f}mm  1m={focus_mm(1):.2f}  2m={focus_mm(2):.2f}  "
          f"5m={focus_mm(5):.2f}  8m={focus_mm(8):.2f}mm")


if __name__ == "__main__":
    main()
