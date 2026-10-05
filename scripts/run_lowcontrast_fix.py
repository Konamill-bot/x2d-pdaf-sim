# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Low-contrast AF without LiDAR: the fix is MEASUREMENT integration, not policy.

Low-contrast failure is measurement-limited (shown earlier: all policies ~equal
and bad). LiDAR is the wrong answer (range ~5 m, most cameras lack it, useless
for distant subjects). The real, software-only fix is TEMPORAL INTEGRATION of
the PDAF signal: when confidence is low, accumulate L/R over K frames so read
noise averages down (~/sqrt(K)) and the faint disparity emerges. Cost: slower.

Tests focus ACQUISITION (static subject at 2 m, lens starts at infinity) on:
  * "faint texture"  -- low-amplitude edges: HAS signal, just buried in noise
  * "flat"           -- essentially no texture: no signal (physics: unfocusable)
with accumulation K = 1, 4, 8. Same adaptive policy throughout.

Run:  python scripts/run_lowcontrast_fix.py  ->  out/lowcontrast_fix.png
"""
from __future__ import annotations
import os, sys
from dataclasses import dataclass, field
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdaf_sim.scene import high_contrast
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.policy import Decision
from pdaf_sim.latency import LatencyBuffer

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)
FPS = 60; N = 150; N_ZONES = 14; MAX_DISP_PX = 48
LENS_LO, LENS_HI = 0.0, 7.0
MAX_STEP_MM = 10000.0 / 300.0 / FPS
MOTOR_RESPONSE = 0.6; LATENCY = 3; UPDATE_EVERY = 2; DEADBAND = 0.08
TARGET_MM = F_MM ** 2 / (2000.0 - F_MM)      # subject static at 2 m


def faint_scene(rng):                         # low-amplitude edges: signal present, buried
    base = high_contrast(rng)
    return (0.5 + 0.12 * (base - base.mean())).astype(np.float32)
def flat_scene(rng):                          # near-no texture: physically unfocusable
    return (0.5 + rng.normal(0, 0.004, (64, 256))).astype(np.float32)


@dataclass
class AdaptivePolicy:
    process_var: float = 0.5 * 15 / FPS; meas_var_base: float = 1.0
    predict_frames: int = LATENCY; step_thresh: float = 0.8; tau_sweep: float = 0.05
    sweep_step: float = 0.3
    _x: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
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
        return Decision(lens_cmd=float(self._x[0] + self._x[1] * self.predict_frames))
    def coast(self):
        F = np.array([[1.0, 1.0], [0.0, 1.0]]); self._x = F @ self._x
        return Decision(lens_cmd=float(self._x[0] + self._x[1] * self.predict_frames))


def measure_accum(sharp, lens, tgt, accum, nrng):
    """Accumulate K noisy L/R renders (temporal integration) -> noise ~/sqrt(K)."""
    Ls = []; Rs = []
    for _ in range(accum):
        L, R = render_lr(sharp, lens - tgt, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                         noise_sigma=0.05, bin_factor=1, rng=nrng)   # high read noise
        Ls.append(L); Rs.append(R)
    L = np.mean(Ls, axis=0); R = np.mean(Rs, axis=0)
    disp, conf = estimate_disparity_multi_zone(L, R, max_disp_px=MAX_DISP_PX, n_zones=N_ZONES)
    return disp / PX_PER_MM, conf


def run(scene_fn, accum, seed=1):
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed + 7)
    sharp = scene_fn(rng); pol = AdaptivePolicy(); lb = LatencyBuffer(LATENCY)
    lens = LENS_LO; last = lens; err = []
    for k in range(N):
        if k % UPDATE_EVERY == 0:
            d, c = measure_accum(sharp, lens, TARGET_MM, accum, nrng)
            arr = lb.push_pop((d, c, lens))
            if arr is not None: last = pol.step(arr[0], arr[1], arr[2]).lens_cmd
            elif hasattr(pol, "coast"): last = pol.coast().lens_cmd
        st = float(np.clip(last - lens, -MAX_STEP_MM, MAX_STEP_MM))
        lens = float(np.clip(lens + MOTOR_RESPONSE * st, LENS_LO, LENS_HI))
        err.append(abs(lens - TARGET_MM))
    return np.array(err)


def main():
    os.makedirs("out", exist_ok=True)
    scenes = [("faint texture (has buried signal)", faint_scene),
              ("flat (no texture = physics)", flat_scene)]
    accums = [(1, "#D85A30", "K=1 (single frame)"),
              (4, "#BA7517", "K=4 integ"),
              (8, "#1D9E75", "K=8 integ")]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5)); k = np.arange(N)
    print(f"{'scene':<34}{'accum':<16}{'final_err(mm)':>14}{'settled':>9}")
    for ax, (sname, sfn) in zip(axes, scenes):
        for K, col, lab in accums:
            e = run(sfn, K)
            ax.plot(k, e, color=col, lw=1.6, label=lab)
            settled = "yes" if np.all(e[-15:] < DEADBAND) else "NO"
            print(f"{sname:<34}{lab:<16}{e[-1]:>14.3f}{settled:>9}")
        ax.axhline(DEADBAND, color="#aaa", ls=":", label="in-focus")
        ax.set_yscale("symlog", linthresh=0.08)
        ax.set_title(sname, fontsize=10); ax.set_xlabel("frame (60fps)")
        ax.set_ylabel("defocus err (mm)"); ax.legend(fontsize=8)
    fig.suptitle("Low-contrast AF without LiDAR: temporal integration (K frames) recovers faint texture; flat is physics",
                 fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.95]); fig.savefig("out/lowcontrast_fix.png", dpi=120)
    print("\nsaved out/lowcontrast_fix.png")


if __name__ == "__main__":
    main()
