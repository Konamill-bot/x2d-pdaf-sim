# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Focus-pull stress: front object -> background, measure HOW LONG AF-C takes.

Real-world hard case:
  * front object at 1.0 mm, background at 5.0 mm
  * user/subject shifts focus front -> bg (frame 45), then bg -> front (frame 130)
  * brightness changes during the pull (no AE lock -> residual noise only)
  * realism: pipeline latency, reduced AF loop rate, motor first-order lag
  * NEW bad thing: FRONT-OBJECT DISTRACTOR -- while the lens is near the front
    object it contaminates the PDAF zone (steals confidence / biases the
    measurement toward the front), so the pull stalls before escaping.

Metric: settle time (frames and ms) after each step = first frame the lens
reaches within deadband of the new target AND stays there.

Compares firmware AF-C (time-filter, NO prediction) vs Kalman (predict=latency).

Run:  python scripts/run_focus_pull.py   ->  out/focus_pull.png
"""
from __future__ import annotations
import os, sys
from dataclasses import dataclass
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdaf_sim.scene import high_contrast
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.policy import TemporalPolicy, Decision
from pdaf_sim.latency import LatencyBuffer

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)
FPS = 60; N = 210; N_ZONES = 14; MAX_DISP_PX = 48
LENS_LO, LENS_HI = -0.5, 6.0
STEPS_PER_MM = 300.0
MAX_STEP_MM = 10000.0 / STEPS_PER_MM / FPS
MOTOR_RESPONSE = 0.6
LATENCY = 3
UPDATE_EVERY = 2
DEADBAND = 0.08

FRONT, BG = 1.0, 5.0
STEP1, STEP2 = 45, 130     # front->bg , bg->front


def target(k):
    if k < STEP1: return FRONT
    if k < STEP2: return BG
    return FRONT

def brightness(k):
    if k < 40: return 1.0
    if k < 70: return float(1.0 - 0.7 * (k - 40) / 30.0)
    if k < 110: return 0.3
    if k < 140: return float(0.3 + 0.7 * (k - 110) / 30.0)
    return 1.0


@dataclass
class FirmwareTimeFilter:
    """Firmware AF-C: time-filter smoothing + CDAF monotonic-scan fallback when
    confidence is low (so it CAN explore toward an out-of-focus target), but NO
    velocity prediction."""
    alpha: float = 0.35
    tau_sweep: float = 0.08
    sweep_step: float = 0.3
    scan_lo: float = LENS_LO
    scan_hi: float = LENS_HI
    _est: float | None = None
    _sweep_dir: int = 1
    def step(self, disparity, confidence, lens_pos):
        if confidence < self.tau_sweep:
            cmd = lens_pos + self._sweep_dir * self.sweep_step
            if cmd > self.scan_hi or cmd < self.scan_lo:
                self._sweep_dir *= -1
                cmd = lens_pos + self._sweep_dir * self.sweep_step
            self._est = cmd
            return Decision(lens_cmd=float(cmd), swept=True)
        z = lens_pos - disparity
        self._est = z if self._est is None else (1 - self.alpha) * self._est + self.alpha * z
        return Decision(lens_cmd=float(self._est))
    def coast(self):
        return Decision(lens_cmd=float(self._est if self._est is not None else 0.0))


def measure(sharp, lens, tgt, br, nrng, rng):
    """PDAF measurement with front-object distractor + light-dependent noise."""
    # Front object contaminates when the lens is near it (front is sharp then).
    front_sharp = float(np.exp(-((lens - FRONT) / 0.6) ** 2))
    plane = tgt
    conf_mult = 1.0
    if tgt != FRONT and rng.random() < 0.55 * front_sharp:
        plane = FRONT                      # distractor grabs the measurement
        conf_mult = 0.6
    else:
        conf_mult = 1.0 - 0.4 * front_sharp if tgt != FRONT else 1.0
    noise = 0.006 + 0.030 * (1.0 - br)
    L, R = render_lr(sharp, lens - plane, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                     noise_sigma=noise, bin_factor=1, rng=nrng)
    disp, conf = estimate_disparity_multi_zone(L, R, max_disp_px=MAX_DISP_PX, n_zones=N_ZONES)
    return disp / PX_PER_MM, float(conf * conf_mult)


def run(make_policy, seed=1):
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed + 7)
    drng = np.random.default_rng(seed + 11)
    sharp = high_contrast(rng); pol = make_policy(); lb = LatencyBuffer(LATENCY)
    lens = FRONT; last = lens
    H = dict(lens=[], tgt=[], err=[], br=[])
    for k in range(N):
        tgt = target(k); br = brightness(k)
        if k % UPDATE_EVERY == 0:
            d, c = measure(sharp, lens, tgt, br, nrng, drng)
            arr = lb.push_pop((d, c, lens))
            if arr is not None:
                last = pol.step(arr[0], arr[1], arr[2]).lens_cmd
            elif hasattr(pol, "coast"):
                last = pol.coast().lens_cmd
        st = float(np.clip(last - lens, -MAX_STEP_MM, MAX_STEP_MM))
        lens = float(np.clip(lens + MOTOR_RESPONSE * st, LENS_LO, LENS_HI))
        H["lens"].append(lens); H["tgt"].append(tgt)
        H["err"].append(abs(lens - tgt)); H["br"].append(br)
    return {kk: np.array(vv) for kk, vv in H.items()}


def settle_ms(err, step_frame, end_frame, hold=8):
    """Frames after step_frame (searching only within [step_frame, end_frame))
    until err<DEADBAND and stays for `hold` frames."""
    for k in range(step_frame, min(end_frame, len(err)) - hold):
        if np.all(err[k:k + hold] < DEADBAND):
            return (k - step_frame), (k - step_frame) / FPS * 1000.0
    return None, None


def main():
    os.makedirs("out", exist_ok=True)
    policies = {
        "firmware AF-C (time-filter)": (FirmwareTimeFilter, "#BA7517"),
        "Kalman (predict=latency)": (lambda: TemporalPolicy(
            tau_sweep=0.05, process_var=0.5 * 15 / FPS, meas_var_base=1.0,
            predict_frames=LATENCY, sweep_step=0.3, scan_lo=LENS_LO, scan_hi=LENS_HI), "#1D9E75"),
    }
    res = {name: (run(mk), col) for name, (mk, col) in policies.items()}
    k = np.arange(N)

    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    ax[0].plot(k, [target(i) for i in k], "k--", lw=1.3, label="focus target")
    ax[0].axhline(FRONT, color="#ccc", lw=0.8); ax[0].axhline(BG, color="#ccc", lw=0.8)
    for x in (STEP1, STEP2): ax[0].axvline(x, color="#bbb", ls=":")
    print(f"{'policy':<30}{'front->bg':>14}{'bg->front':>14}")
    for name, (r, col) in res.items():
        ax[0].plot(k, r["lens"], color=col, lw=1.8, label=name)
        f1, ms1 = settle_ms(r["err"], STEP1, STEP2)
        f2, ms2 = settle_ms(r["err"], STEP2, N)
        s1 = f"{ms1:.0f}ms ({f1}f)" if ms1 else "NO LOCK"
        s2 = f"{ms2:.0f}ms ({f2}f)" if ms2 else "NO LOCK"
        print(f"{name:<30}{s1:>14}{s2:>14}")
        # annotate settle on plot
        if f1: ax[0].annotate(s1, (STEP1 + f1, BG), color=col, fontsize=8,
                              xytext=(STEP1 + f1 + 2, BG - 0.6 if col == "#BA7517" else BG - 1.1))
    ax[0].set_ylabel("lens / target (mm)"); ax[0].set_ylim(0, 6)
    ax[0].set_title("Focus pull: front (1.0mm) -> background (5.0mm) -> front  | latency=3, rate=1/2, front-distractor")
    ax[0].legend(fontsize=8, loc="center right")

    for name, (r, col) in res.items():
        ax[1].plot(k, r["err"], color=col, lw=1.4, label=name)
    ax[1].axhline(DEADBAND, color="#aaa", ls=":"); ax[1].set_yscale("symlog", linthresh=0.08)
    axb = ax[1].twinx(); axb.plot(k, [brightness(i) for i in k], color="#ddd", lw=1.0); axb.set_ylabel("brightness", color="#bbb")
    for x in (STEP1, STEP2): ax[1].axvline(x, color="#bbb", ls=":")
    ax[1].set_ylabel("focus err (mm)"); ax[1].set_xlabel("frame (60fps)")
    ax[1].set_title("Focus error (grey = brightness)")
    ax[1].legend(fontsize=8)
    fig.tight_layout(); fig.savefig("out/focus_pull.png", dpi=120)
    print("\nsaved out/focus_pull.png")


if __name__ == "__main__":
    main()
