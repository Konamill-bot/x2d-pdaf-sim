# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""The core AF-C job, NORMAL conditions: a moving subject (walking in) through
brief OCCLUSION dropouts, good light + contrast. Three policies:

  1. firmware        -- baseline AF-C (time-filter + sweep), 30 Hz loop
  2. Kalman (mine)   -- predictive temporal filter, 30 Hz loop
  3. X2D+            -- adaptive (step-clamp + confidence-scaled predict + coast)
                        at 60 Hz loop + bias fix (the full proposal)

Question: does the baseline keep focus on the subject's focus zone as it moves
and is briefly occluded, or does it lag / lose it? Metric = in-focus % and focus
error; dropout windows shaded.

Reuses the policies/measurement from run_x2d_plus and the repo PDAF stack.
Run:  python scripts/run_moving_dropout.py   ->  out/moving_dropout.png
"""
from __future__ import annotations
import importlib.util, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_x2d_plus", os.path.join(HERE, "run_x2d_plus.py"))
X = importlib.util.module_from_spec(spec); sys.modules["run_x2d_plus"] = X; spec.loader.exec_module(X)
from pdaf_sim.scene import high_contrast
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone
from pdaf_sim.policy import TemporalPolicy

FPS = 60; N = 780; LO, HI = X.LO, X.HI                 # 13 s
F, FN, DI, PIX = X.F_MM, X.FNUM, X.DIST, X.PIX
PPM = X.PX_PER_MM; LAT = X.LATENCY; DB = X.DEADBAND
DROPOUTS = [(170, 230), (380, 450), (560, 640)]       # ~1 s occlusions


def subj_m(k): return 4.0 - 3.1 * (k / N)             # brisk walk-in 4.0 m -> 0.9 m
def in_drop(k): return any(a <= k < b for a, b in DROPOUTS)


class KalmanMine:                                      # the user's predictive filter
    def __init__(s):
        s.p = TemporalPolicy(tau_sweep=0.05, process_var=0.5*15/FPS, meas_var_base=1.0,
                             predict_frames=LAT, sweep_step=0.3, scan_lo=LO, scan_hi=HI)
    def step(s, d, c, cs, lens): return s.p.step(d, c, lens).lens_cmd
    def coast(s): return s.p.coast().lens_cmd


def run(make_policy, update_every, speed, seed=1):
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed+7)
    sharp = high_contrast(rng); pol = make_policy(); lb = X.LatencyBuffer(LAT)
    lens = X.focus_mm(subj_m(0)); last = lens
    motor = X.make_motor(lens, speed)                  # magnetic voice-coil at this speed cap
    track = np.zeros(N); err = np.zeros(N)
    for k in range(N):
        tgt = X.focus_mm(subj_m(k))
        if k % update_every == 0:
            if in_drop(k):
                last = pol.coast()
            else:
                L, R = render_lr(sharp, lens-tgt, F, FN, DI, PIX, noise_sigma=0.01, rng=nrng)
                d, c = estimate_disparity_multi_zone(L, R, max_disp_px=48, n_zones=14)
                arr = lb.push_pop((d/PPM, c, 0.0, lens))
                last = pol.coast() if (arr is None or arr[0] is None) else pol.step(arr[0], arr[1], arr[2], arr[3])
        lens = motor.command(last)                     # real motor dynamics
        track[k] = lens; err[k] = abs(lens-tgt)
    return track, err


def main():
    os.makedirs("out", exist_ok=True)
    # firmware & Kalman run on X2D's current hardware (4000); X2D+ = proposal (10000)
    cfgs = [("firmware (30Hz, 4000)", lambda: X.Firmware(), 2, X.SPEED_X2D, "#D85A30"),
            ("Kalman mine (30Hz, 4000)", KalmanMine, 2, X.SPEED_X2D, "#378ADD"),
            ("X2D+ (60Hz, 10000)", lambda: X.X2DPlus(), 1, X.SPEED_TARGET, "#1D9E75")]
    k = np.arange(N); t = k / FPS
    res = {}
    for name, mk, ue, spd, col in cfgs:
        track, err = run(mk, ue, spd); res[name] = (track, err, col)
        print(f"{name:<24} in-focus={100*np.mean(err<DB):5.1f}%  mean_err={err.mean():.3f}mm  "
              f"dropout in-focus={100*np.mean(err[[in_drop(i) for i in k]]<DB):5.1f}%")
    fig, ax = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    ax[0].plot(t, [X.focus_mm(subj_m(i)) for i in k], "k--", lw=1.4, label="subject focus zone")
    for name, (tr, er, col) in res.items(): ax[0].plot(t, tr, color=col, lw=1.5, label=name)
    for a, b in DROPOUTS: ax[0].axvspan(a/FPS, b/FPS, color="#ccc", alpha=0.5)
    ax[0].set_ylabel("lens / subject (mm)"); ax[0].legend(fontsize=8, loc="upper right")
    ax[0].set_title("Moving subject + occlusion dropouts (grey) — does AF hold the focus zone?")
    for name, (tr, er, col) in res.items(): ax[1].plot(t, er, color=col, lw=1.3, label=name)
    for a, b in DROPOUTS: ax[1].axvspan(a/FPS, b/FPS, color="#ccc", alpha=0.5)
    ax[1].axhline(DB, color="#aaa", ls=":"); ax[1].set_yscale("symlog", linthresh=0.1)
    ax[1].set_ylabel("focus err (mm)"); ax[1].set_xlabel("time (s)"); ax[1].legend(fontsize=8)
    fig.suptitle("Core AF-C: moving subject + dropout — firmware vs Kalman vs X2D+ (60Hz+bias fix)", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.97]); fig.savefig("out/moving_dropout.png", dpi=120)
    print("saved out/moving_dropout.png")


if __name__ == "__main__":
    main()
