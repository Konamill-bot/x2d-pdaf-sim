"""Full-stack lever-by-lever experiment.

The previous 'stacked_comparison' and v3 figures each tested a subset of
the proposed firmware improvements. This script tests the marginal
contribution of EVERY lever, stacked progressively:

  cfg A : Stateless,    bin=1,  single-zone, no CDAF   <- current X2D-like
  cfg B : + 4x4 binning            (Lever 1)
  cfg C : + Kalman temporal prior  (Lever 2)
  cfg D : + multi-zone confidence  (Lever 3)
  cfg E : + V3 deadband + PID + CDAF fusion  (Lever 4)

For each, run on T1 low_contrast and high_contrast at the appropriate
fps (15 fps for bin=1, 60 fps for bin=4). Report sweeps, time-to-lock,
final error, and lens travel.

This is the honest version of the headline figure that goes into
TECHNICAL_PROPOSAL.md. If a lever HURTS, we report that too -- the goal
is to know what actually helps, not to make a clean marketing graph.
"""
from __future__ import annotations
import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import (estimate_disparity,
                                 estimate_disparity_multi_zone, cdaf_score)
from pdaf_sim.policy import StatelessPolicy, TemporalPolicy, V3Policy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.scene import SCENES

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)
HORIZON_SECONDS = 2.0
BIN_TO_FPS = {1: 15, 4: 60}


def run(scene_name: str, *, bin_factor: int, policy,
        use_multi_zone: bool, uses_cdaf: bool, seed: int = 1):
    fps = BIN_TO_FPS[bin_factor]
    n_frames = int(HORIZON_SECONDS * fps)
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    sharp = SCENES[scene_name](rng)
    truth = np.full(n_frames, 2.5)
    lens_mm = 0.0
    cmds, sweeps = [], 0

    for k in range(n_frames):
        err_mm = lens_mm - truth[k]
        L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                         noise_sigma=0.01, bin_factor=bin_factor,
                         rng=noise_rng)
        if use_multi_zone:
            disp_px, conf = estimate_disparity_multi_zone(L, R)
        else:
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

    cmds = np.array(cmds)
    t = np.arange(n_frames) / fps
    in_focus = np.abs(cmds - 2.5) < 0.3
    t_focus = float(t[np.argmax(in_focus)]) if in_focus.any() else float('nan')
    travel = float(np.sum(np.abs(np.diff(cmds))))
    return {'t': t, 'cmds': cmds, 'sweeps': sweeps, 't_focus': t_focus,
            'final_err': float(abs(cmds[-1] - 2.5)),
            'in_focus_pct': 100 * float(in_focus.mean()),
            'travel_mm': travel}


def build_configs():
    """Return list of (label, kwargs) for run()."""
    return [
        ('A baseline (stateless, bin1, single-zone)',
         dict(bin_factor=1, policy=StatelessPolicy(tau=0.35, sweep_step=0.2),
              use_multi_zone=False, uses_cdaf=False)),
        ('B + 4x4 binning (15->60 fps)',
         dict(bin_factor=4, policy=StatelessPolicy(tau=0.35, sweep_step=0.2),
              use_multi_zone=False, uses_cdaf=False)),
        ('C + Kalman temporal prior',
         dict(bin_factor=4,
              policy=TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                                    process_var=0.5*15/60, meas_var_base=1.0),
              use_multi_zone=False, uses_cdaf=False)),
        ('D + multi-zone confidence',
         dict(bin_factor=4,
              policy=TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                                    process_var=0.5*15/60, meas_var_base=1.0),
              use_multi_zone=True, uses_cdaf=False)),
        ('E + deadband + PID + CDAF fusion (V3Policy)',
         dict(bin_factor=4, policy=V3Policy(),
              use_multi_zone=True, uses_cdaf=True)),
    ]


def main():
    os.makedirs('out', exist_ok=True)
    scenes = ['low_contrast', 'high_contrast']

    all_results = {scene: [] for scene in scenes}
    for scene in scenes:
        for label, kwargs in build_configs():
            # Fresh policy instance per run (Kalman/PID have state)
            kw = dict(kwargs)
            # rebuild policy each time to clear state
            kw['policy'] = type(kw['policy'])(**{
                k: v for k, v in kw['policy'].__dict__.items()
                if not k.startswith('_') and not callable(v)
            })
            r = run(scene, **kw)
            all_results[scene].append((label, r))

    # Print table
    for scene in scenes:
        print(f"\n=== {scene} ===")
        print(f"{'config':<48}{'sweeps':>8}{'t_focus_s':>12}"
              f"{'final_err_mm':>14}{'in_focus%':>11}{'travel_mm':>12}")
        print('-' * 105)
        for label, r in all_results[scene]:
            print(f"{label:<48}{r['sweeps']:>8d}"
                  f"{r['t_focus']:>12.3f}{r['final_err']:>14.3f}"
                  f"{r['in_focus_pct']:>10.1f}%{r['travel_mm']:>12.2f}")

    # Plot
    fig, axes = plt.subplots(len(scenes), 1, figsize=(12, 4*len(scenes)),
                             sharex=True)
    if len(scenes) == 1:
        axes = [axes]
    colors = ['C3', 'C1', 'C0', 'C4', 'C2']
    for ax, scene in zip(axes, scenes):
        ax.axhline(2.5, color='k', linestyle='--', alpha=0.5, label='truth = 2.5 mm')
        for (label, r), c in zip(all_results[scene], colors):
            tf = (f'{r["t_focus"]:.2f}s' if not np.isnan(r["t_focus"])
                  else 'no lock')
            ax.plot(r['t'], r['cmds'], color=c, linewidth=1.4, alpha=0.9,
                    label=f"{label}\n   sweeps={r['sweeps']}, lock={tf}, "
                          f"err={r['final_err']:.2f}mm")
        ax.set_ylabel('lens pos (mm)')
        ax.set_title(scene)
        ax.legend(loc='best', fontsize=7)
    axes[-1].set_xlabel('time (s)')
    fig.suptitle('Marginal contribution of each lever, stacked progressively',
                 fontsize=12)
    fig.tight_layout()
    out = os.path.join('out', 'full_stack.png')
    fig.savefig(out, dpi=130)
    print(f"\nSaved -> {out}")


if __name__ == '__main__':
    main()
