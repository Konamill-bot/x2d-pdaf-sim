"""v3 comparison: Stateless / Temporal v1 (Kalman) / V3 (Kalman+Deadband+PID+CDAF fusion).

Same low_contrast + high_contrast scenes as the stacked experiment,
all at 60 fps with 4x4 binning (since we already established that's a
prerequisite for any temporal-prior policy to work). The difference here
is policy SOPHISTICATION at the AF compute layer, not the readout layer.

Run:  python scripts/run_v3_comparison.py
Out:  out/v3_comparison.png
"""
from __future__ import annotations
import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity, cdaf_score
from pdaf_sim.policy import StatelessPolicy, TemporalPolicy, V3Policy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.scene import SCENES

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)

FPS = 60
BIN = 4
HORIZON_SECONDS = 2.0


def run(scene_name: str, policy, uses_cdaf: bool, seed: int = 1):
    n_frames = int(HORIZON_SECONDS * FPS)
    rng = np.random.default_rng(seed)
    sharp = SCENES[scene_name](rng)
    truth_mm = np.full(n_frames, 2.5)
    lens_mm = 0.0
    cmds, t, sweeps = [], [], 0
    for k in range(n_frames):
        err_mm = lens_mm - truth_mm[k]
        L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                         noise_sigma=0.01, bin_factor=BIN)
        disp_px, conf = estimate_disparity(L, R)
        disp_mm = disp_px / PX_PER_MM
        if uses_cdaf:
            sharpness = cdaf_score((L + R) * 0.5)
            decision = policy.step(disp_mm, conf, sharpness, lens_mm)
        else:
            decision = policy.step(disp_mm, conf, lens_mm)
        if decision.swept:
            sweeps += 1
        lens_mm = decision.lens_cmd
        cmds.append(lens_mm)
        t.append(k / FPS)
    cmds = np.array(cmds)
    t = np.array(t)
    in_focus_mask = np.abs(cmds - 2.5) < 0.3
    t_to_focus = float(t[np.argmax(in_focus_mask)]) if in_focus_mask.any() else float('nan')
    deltas = np.diff(cmds)
    hunt_osc = int(np.sum((np.abs(deltas[:-1]) > 0.05) &
                          (np.sign(deltas[:-1]) != np.sign(deltas[1:]))))
    motion = float(np.sum(np.abs(deltas)))     # total lens travel
    return {'t': t, 'cmds': cmds, 'sweeps': sweeps, 't_to_focus': t_to_focus,
            'final_err': float(abs(cmds[-1] - 2.5)),
            'hunt_osc': hunt_osc, 'motion': motion}


def main():
    os.makedirs('out', exist_ok=True)
    scenes = ['low_contrast', 'high_contrast']

    print(f"\n{'scene':<14}{'policy':<28}{'sweeps':>8}{'hunt_osc':>10}"
          f"{'t_focus_s':>12}{'final_err_mm':>14}{'lens_travel':>14}")
    print('-' * 100)

    fig, axes = plt.subplots(len(scenes), 1, figsize=(11, 4 * len(scenes)),
                             sharex=True)
    if len(scenes) == 1:
        axes = [axes]

    for ax, scene in zip(axes, scenes):
        results = {
            'Stateless (15fps, full readout)':
                run(scene, StatelessPolicy(tau=0.35, sweep_step=0.2), uses_cdaf=False),
            'v1: Kalman + 4x4 bin @ 60fps':
                run(scene, TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                                          process_var=0.5*15/FPS, meas_var_base=1.0),
                    uses_cdaf=False),
            'v3: + deadband + PID + CDAF fusion':
                run(scene, V3Policy(), uses_cdaf=True),
        }
        for name, r in results.items():
            print(f"{scene:<14}{name:<28}{r['sweeps']:>8d}{r['hunt_osc']:>10d}"
                  f"{r['t_to_focus']:>12.3f}{r['final_err']:>14.3f}{r['motion']:>14.2f}")
        print()

        ax.axhline(2.5, color='k', linestyle='--', alpha=0.5, label='truth = 2.5 mm')
        colors = {'Stateless (15fps, full readout)': 'C3',
                  'v1: Kalman + 4x4 bin @ 60fps': 'C0',
                  'v3: + deadband + PID + CDAF fusion': 'C2'}
        for name, r in results.items():
            tf = (f'{r["t_to_focus"]:.2f}s' if not np.isnan(r["t_to_focus"]) else 'no lock')
            ax.plot(r['t'][:len(r['cmds'])] if name.startswith('Stateless')
                    else np.arange(len(r['cmds']))/FPS,
                    r['cmds'], color=colors[name], linewidth=1.4,
                    label=f"{name}\n   sweeps={r['sweeps']}, "
                          f"hunt_osc={r['hunt_osc']}, lock={tf}, "
                          f"travel={r['motion']:.1f}mm")
        ax.set_title(f'{scene}', fontsize=11)
        ax.set_ylabel('lens pos (mm)')
        ax.legend(loc='best', fontsize=8)
    axes[-1].set_xlabel('time (s)')
    fig.suptitle('v3 stack: Kalman + deadband + PID + PDAF/CDAF fusion',
                 fontsize=12)
    fig.tight_layout()
    out = os.path.join('out', 'v3_comparison.png')
    fig.savefig(out, dpi=130)
    print(f"\nSaved -> {out}")


if __name__ == '__main__':
    main()
