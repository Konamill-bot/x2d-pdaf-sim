"""Effect of AF-readout binning on the Temporal v1 policy.

Hypothesis: AF readout binning (4x4) reduces ISP load -> can sample PDAF
at higher framerate -> Kalman temporal prior gets more measurements per
unit time -> faster lock + less hunting, with no precision penalty
because PDAF doesn't need sub-pixel accuracy at AF stage.

We simulate the framerate gain by running the SAME real-world time
horizon at three framerates corresponding to bin factors 1 / 2 / 4
(approx 15 / 30 / 60 fps for a 100MP-class sensor).
"""
from __future__ import annotations
import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity
from pdaf_sim.policy import TemporalPolicy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.scene import SCENES

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)

# Approx framerates the X2D-class sensor can sustain at each bin level.
# bin=1 -> ~15 fps (100MP readout dominates), bin=2 -> ~30 fps, bin=4 -> ~60 fps.
BIN_TO_FPS = {1: 15, 2: 30, 4: 60}
HORIZON_SECONDS = 2.0


def simulate(bin_factor: int, seed: int = 1):
    fps = BIN_TO_FPS[bin_factor]
    n_frames = int(HORIZON_SECONDS * fps)

    rng = np.random.default_rng(seed)
    sharp = SCENES['low_contrast'](rng)

    # Truth: subject at 2.5mm, lens starts mis-focused at 0.
    truth_mm = np.full(n_frames, 2.5)
    lens_mm = 0.0
    policy = TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                            process_var=0.5 / fps * 15,   # scale to keep
                            meas_var_base=1.0)            # noise consistent

    cmds, t = [], []
    sweeps = 0
    for k in range(n_frames):
        err_mm = lens_mm - truth_mm[k]
        L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                         noise_sigma=0.01, bin_factor=bin_factor)
        disp_px, conf = estimate_disparity(L, R)
        disp_mm = disp_px / PX_PER_MM
        decision = policy.step(disp_mm, conf, lens_mm)
        if decision.swept:
            sweeps += 1
        lens_mm = decision.lens_cmd
        cmds.append(lens_mm)
        t.append(k / fps)
    return np.array(t), np.array(cmds), sweeps


def main():
    os.makedirs('out', exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 5))
    print(f"\n{'bin':<6}{'fps':>6}{'frames/2s':>12}{'sweeps':>10}"
          f"{'final_err_mm':>16}{'time_to_focus_s':>18}")
    print('-' * 70)
    for bf in (1, 2, 4):
        t, cmds, sweeps = simulate(bf)
        final_err = abs(cmds[-1] - 2.5)
        in_focus_mask = np.abs(cmds - 2.5) < 0.3
        if in_focus_mask.any():
            t_to_focus = float(t[np.argmax(in_focus_mask)])
        else:
            t_to_focus = float('nan')
        print(f"{bf}x{bf:<3}{BIN_TO_FPS[bf]:>6}{len(t):>12}{sweeps:>10}"
              f"{final_err:>16.3f}{t_to_focus:>18.3f}")
        ax.plot(t, cmds, label=f'bin={bf}x{bf}  ({BIN_TO_FPS[bf]} fps)', linewidth=1.2)
    ax.axhline(2.5, color='k', linestyle='--', alpha=0.5, label='truth')
    ax.set_xlabel('time (s)')
    ax.set_ylabel('lens pos (mm)')
    ax.set_title('AF readout binning -> framerate -> Temporal-prior convergence')
    ax.legend(loc='best', fontsize=9)
    fig.tight_layout()
    out = os.path.join('out', 'binning_sweep.png')
    fig.savefig(out, dpi=130)
    print(f"\nSaved -> {out}")


if __name__ == '__main__':
    main()
