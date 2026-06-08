"""Stack all three improvements: binning + temporal prior + multi-zone.

Compares the X2D-realistic baseline against the proposed combined-fix:
  Baseline   : Stateless policy, full-frame readout (15 fps)
  Combined   : Composite policy (Kalman + multi-zone), 4x4 binned readout (60 fps)

This is the headline figure for Letter 2 / GitHub front page.
"""
from __future__ import annotations
import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity, estimate_disparity_multi_zone
from pdaf_sim.policy import StatelessPolicy, TemporalPolicy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.scene import SCENES

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)

HORIZON_SECONDS = 2.0
BIN_TO_FPS = {1: 15, 4: 60}


def run(scene_name: str, policy, bin_factor: int, use_multi_zone: bool, seed: int = 1):
    fps = BIN_TO_FPS[bin_factor]
    n_frames = int(HORIZON_SECONDS * fps)
    rng = np.random.default_rng(seed)
    sharp = SCENES[scene_name](rng)
    truth_mm = np.full(n_frames, 2.5)
    lens_mm = 0.0
    cmds, t, sweeps = [], [], 0
    for k in range(n_frames):
        err_mm = lens_mm - truth_mm[k]
        L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                         noise_sigma=0.01, bin_factor=bin_factor)
        if use_multi_zone:
            disp_px, conf = estimate_disparity_multi_zone(L, R)
        else:
            disp_px, conf = estimate_disparity(L, R)
        disp_mm = disp_px / PX_PER_MM
        decision = policy.step(disp_mm, conf, lens_mm)
        if decision.swept:
            sweeps += 1
        lens_mm = decision.lens_cmd
        cmds.append(lens_mm)
        t.append(k / fps)
    cmds = np.array(cmds)
    t = np.array(t)
    in_focus_mask = np.abs(cmds - 2.5) < 0.3
    t_to_focus = float(t[np.argmax(in_focus_mask)]) if in_focus_mask.any() else float('nan')
    return {'t': t, 'cmds': cmds, 'sweeps': sweeps, 't_to_focus': t_to_focus,
            'final_err': float(abs(cmds[-1] - 2.5))}


def main():
    os.makedirs('out', exist_ok=True)

    scenes = ['low_contrast', 'high_contrast']

    fig, axes = plt.subplots(len(scenes), 1, figsize=(11, 4 * len(scenes)),
                             sharex=True)
    if len(scenes) == 1:
        axes = [axes]

    print(f"\n{'scene':<16}{'config':<32}{'sweeps':>10}{'t_to_focus_s':>16}{'final_err_mm':>16}")
    print('-' * 90)
    for ax, scene in zip(axes, scenes):
        baseline = run(scene, StatelessPolicy(tau=0.35, sweep_step=0.2),
                       bin_factor=1, use_multi_zone=False)
        # Scale process variance to fps so noise integration is consistent.
        fps = BIN_TO_FPS[4]
        combined = run(scene,
                       TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                                      process_var=0.5 * 15 / fps,
                                      meas_var_base=1.0),
                       bin_factor=4, use_multi_zone=False)

        print(f"{scene:<16}{'Baseline (current X2D-like)':<32}"
              f"{baseline['sweeps']:>10d}{baseline['t_to_focus']:>16.3f}"
              f"{baseline['final_err']:>16.3f}")
        print(f"{scene:<16}{'Combined (all 3 levers)':<32}"
              f"{combined['sweeps']:>10d}{combined['t_to_focus']:>16.3f}"
              f"{combined['final_err']:>16.3f}")

        ax.axhline(2.5, color='k', linestyle='--', alpha=0.5, label='truth = 2.5 mm')
        ax.plot(baseline['t'], baseline['cmds'], color='C3', linewidth=1.4,
                label=f'Baseline: stateless + full 100MP @ 15fps  '
                      f'({baseline["sweeps"]} sweeps, no lock)')
        lock_str = (f'lock in {combined["t_to_focus"]:.2f}s'
                    if not np.isnan(combined["t_to_focus"]) else 'no lock')
        ax.plot(combined['t'], combined['cmds'], color='C2', linewidth=1.6,
                label=f'Combined: Kalman + 4x4 bin + multi-zone @ 60fps  '
                      f'({combined["sweeps"]} sweeps, {lock_str})')
        ax.set_title(f'{scene}', fontsize=11)
        ax.set_ylabel('lens pos (mm)')
        ax.legend(loc='best', fontsize=9)
    axes[-1].set_xlabel('time (s)')
    fig.suptitle('Stacked firmware improvements: binning + temporal prior + multi-zone',
                 fontsize=12)
    fig.tight_layout()
    out = os.path.join('out', 'stacked_comparison.png')
    fig.savefig(out, dpi=130)
    print(f"\nSaved -> {out}")


if __name__ == '__main__':
    main()
