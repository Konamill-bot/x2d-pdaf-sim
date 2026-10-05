# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Why AF-C is STILL not good even with the time-filter path enabled:
the sim-so-far assumed zero latency, instant motor, every-frame updates.
Real pipeline has (1) loop-rate sub-sampling, (2) pipeline LATENCY,
(3) motor lag. This sweeps those and shows the firmware time-filter
(exp smoothing, NO prediction) degrades, while a PREDICTIVE Kalman
(predict_frames = latency) stays good -- i.e. the remaining gap is
TIMING + lack of prediction, both software.

Reuses: scene/dualpixel/phase_corr + policy.TemporalPolicy + latency.LatencyBuffer.

Run:  python scripts/run_realism_sweep.py   ->  out/realism_sweep.png
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
FPS = 60; N = 200; N_ZONES = 14; MAX_DISP_PX = 40
LENS_LO, LENS_HI = -0.5, 6.0
STEPS_PER_MM = 300.0
MAX_STEP_MM = 10000.0 / STEPS_PER_MM / FPS
MOTOR_RESPONSE = 0.6        # first-order motor: lens moves this fraction toward cmd/frame


@dataclass
class FirmwareTimeFilter:
    """Firmware AF-C temporal layer: exp smoothing (exponential time filter),
    NO velocity prediction."""
    alpha: float = 0.35
    _est: float | None = None
    def step(self, disparity, confidence, lens_pos):
        z = lens_pos - disparity
        self._est = z if self._est is None else (1 - self.alpha) * self._est + self.alpha * z
        return Decision(lens_cmd=float(self._est))
    def coast(self):
        return Decision(lens_cmd=float(self._est if self._est is not None else 0.0))


def subject(k):     # steady moving subject (isolate the timing effect), good light
    return float(np.clip(1.5 + 3.0 * (k / N), LENS_LO, LENS_HI))


def simulate(make_policy, latency, update_every, seed=1):
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed + 7)
    sharp = high_contrast(rng)
    pol = make_policy()
    lb = LatencyBuffer(latency)
    lens = subject(0); errs = []
    last_cmd = lens
    for k in range(N):
        subj = subject(k); err_mm = lens - subj
        if k % update_every == 0:                       # AF loop runs at reduced rate
            L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                             noise_sigma=0.008, bin_factor=1, rng=nrng)
            disp, conf = estimate_disparity_multi_zone(L, R, max_disp_px=MAX_DISP_PX, n_zones=N_ZONES)
            arrived = lb.push_pop((disp / PX_PER_MM, conf, lens))   # delay by `latency`
            if arrived is not None:
                d_a, c_a, lens_at = arrived
                last_cmd = pol.step(d_a, c_a, lens_at).lens_cmd
            elif hasattr(pol, "coast"):
                last_cmd = pol.coast().lens_cmd
        step = float(np.clip(last_cmd - lens, -MAX_STEP_MM, MAX_STEP_MM))
        lens = float(np.clip(lens + MOTOR_RESPONSE * step, LENS_LO, LENS_HI))
        errs.append(abs(lens - subj))
    return np.array(errs)


def mk_firmware():
    return FirmwareTimeFilter()
def mk_kalman(predict):
    return TemporalPolicy(tau_sweep=0.0, process_var=0.5 * 15 / FPS,
                          meas_var_base=1.0, predict_frames=predict)


def main():
    os.makedirs("out", exist_ok=True)
    fig, ax = plt.subplots(1, 3, figsize=(16, 5))

    # (1) error vs pipeline latency
    lats = list(range(0, 8))
    fw = [simulate(mk_firmware, L, 1).mean() for L in lats]
    kf0 = [simulate(lambda: mk_kalman(0), L, 1).mean() for L in lats]
    kfp = [simulate(lambda L=L: mk_kalman(L), L, 1).mean() for L in lats]   # predict = latency
    ax[0].plot(lats, fw, "o-", color="#BA7517", label="firmware time-filter (no predict)")
    ax[0].plot(lats, kf0, "s-", color="#378ADD", label="Kalman, predict=0")
    ax[0].plot(lats, kfp, "^-", color="#1D9E75", label="Kalman, predict=latency")
    ax[0].set_xlabel("pipeline latency (frames)"); ax[0].set_ylabel("mean focus err (mm)")
    ax[0].set_title("Error vs latency (moving subject)"); ax[0].legend(fontsize=8)

    # (2) error vs AF loop update interval (1 = every frame, 4 = 1/4 rate)
    ivals = [1, 2, 3, 4, 6, 8]
    fw2 = [simulate(mk_firmware, 2, m).mean() for m in ivals]
    kf2 = [simulate(lambda: mk_kalman(2), 2, m).mean() for m in ivals]
    ax[1].plot(ivals, fw2, "o-", color="#BA7517", label="firmware time-filter")
    ax[1].plot(ivals, kf2, "^-", color="#1D9E75", label="Kalman predict=2")
    ax[1].set_xlabel("AF update every N frames (lower rate ->)"); ax[1].set_ylabel("mean focus err (mm)")
    ax[1].set_title("Error vs AF loop rate (latency=2)"); ax[1].legend(fontsize=8)

    # (3) example trajectories at realistic latency=3, rate=2
    k = np.arange(N)
    ax[2].plot(k, [subject(i) for i in k], "k--", lw=1, label="subject")
    for mk, lab, col in [(mk_firmware, "firmware time-filter", "#BA7517"),
                         (lambda: mk_kalman(3), "Kalman predict=3", "#1D9E75")]:
        # re-run storing lens trajectory
        rng = np.random.default_rng(1); nrng = np.random.default_rng(8)
        sharp = high_contrast(rng); pol = mk(); lb = LatencyBuffer(3)
        lens = subject(0); last = lens; traj = []
        for i in range(N):
            subj = subject(i); em = lens - subj
            if i % 2 == 0:
                L, R = render_lr(sharp, em, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM, noise_sigma=0.008, rng=nrng)
                d, c = estimate_disparity_multi_zone(L, R, max_disp_px=MAX_DISP_PX, n_zones=N_ZONES)
                arr = lb.push_pop((d / PX_PER_MM, c, lens))
                if arr is not None: last = pol.step(arr[0], arr[1], arr[2]).lens_cmd
                elif hasattr(pol, "coast"): last = pol.coast().lens_cmd
            st = float(np.clip(last - lens, -MAX_STEP_MM, MAX_STEP_MM))
            lens = float(np.clip(lens + MOTOR_RESPONSE * st, LENS_LO, LENS_HI)); traj.append(lens)
        ax[2].plot(k, traj, color=col, lw=1.6, label=lab)
    ax[2].set_xlabel("frame"); ax[2].set_ylabel("lens pos (mm)")
    ax[2].set_title("Trajectory @ latency=3, rate=1/2"); ax[2].legend(fontsize=8)

    fig.suptitle("Why AF-C stays bad: loop rate + latency + no prediction (not silicon)", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96]); fig.savefig("out/realism_sweep.png", dpi=120)
    print("saved out/realism_sweep.png")
    print(f"\nlatency=0: firmware={fw[0]:.3f}  kalman_p=latency={kfp[0]:.3f}")
    print(f"latency=4: firmware={fw[4]:.3f}  kalman_p0={kf0[4]:.3f}  kalman_p=latency={kfp[4]:.3f}")


if __name__ == "__main__":
    main()
