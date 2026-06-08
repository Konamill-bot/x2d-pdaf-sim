"""Compare Stateless / Temporal v1 / Composite v2 on all benchmarks.

The Composite v2 policy uses:
  - multi-zone confidence (estimate_disparity_multi_zone)
  - near-focus bonus
  - temporal accumulator (require N high-conf frames before locking)
  - sticky-in-focus (tolerate brief low-conf without sweeping)

Run with:  python scripts/run_v2_comparison.py
Outputs:   out/v2_comparison.png + console summary table.
"""
from __future__ import annotations
import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity, estimate_disparity_multi_zone
from pdaf_sim.policy import StatelessPolicy, TemporalPolicy, CompositePolicy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.scene import SCENES
from pdaf_sim.benchmarks import ALL_BENCHMARKS

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)


def simulate(bench, policy, use_multi_zone: bool, seed: int = 1):
    rng = np.random.default_rng(seed)
    sharp = SCENES[bench.scene](rng)
    lens_mm = 0.0
    cmds, sweeps = [], 0
    for k in range(bench.n_frames):
        err_mm = lens_mm - bench.truth_mm[k]
        L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM, noise_sigma=0.01)
        if use_multi_zone:
            disp_px, conf = estimate_disparity_multi_zone(L, R)
        else:
            disp_px, conf = estimate_disparity(L, R)
        disp_mm = disp_px / PX_PER_MM if abs(PX_PER_MM) > 1e-9 else 0.0
        decision = policy.step(disp_mm, conf, lens_mm)
        if decision.swept:
            sweeps += 1
        lens_mm = decision.lens_cmd
        cmds.append(lens_mm)
        if k in bench.shutter_frames and hasattr(policy, 'on_shutter'):
            policy.on_shutter()
    cmds = np.array(cmds)
    rms = float(np.sqrt(np.mean((cmds - bench.truth_mm) ** 2)))
    in_focus = float(np.mean(np.abs(cmds - bench.truth_mm) < 0.3))
    deltas = np.diff(cmds)
    hunt_osc = int(np.sum((np.abs(deltas[:-1]) > 0.05) &
                          (np.sign(deltas[:-1]) != np.sign(deltas[1:]))))
    return {'cmds': cmds, 'truth': bench.truth_mm,
            'sweeps': sweeps, 'hunt_osc': hunt_osc,
            'rms_err_mm': rms, 'in_focus_frac': in_focus}


def main():
    os.makedirs('out', exist_ok=True)
    print(f"\n{'benchmark':<28}{'policy':<14}{'sweeps':>8}{'hunt':>8}{'rms_mm':>10}{'in_focus%':>12}")
    print('-' * 80)

    results = {}
    for bf in ALL_BENCHMARKS:
        bench = bf()
        results[bench.name] = {
            'Stateless':   simulate(bench, StatelessPolicy(tau=0.35, sweep_step=0.2), False),
            'Temporal v1': simulate(bench, TemporalPolicy(tau_sweep=0.08, sweep_step=0.2), False),
            'Composite v2': simulate(bench, CompositePolicy(), True),
        }
        for name, r in results[bench.name].items():
            print(f"{bench.name:<28}{name:<14}{r['sweeps']:>8d}{r['hunt_osc']:>8d}"
                  f"{r['rms_err_mm']:>10.3f}{100*r['in_focus_frac']:>11.1f}%")
        print()

    n = len(results)
    fig, axes = plt.subplots(n, 1, figsize=(11, 2.2 * n), sharex=False)
    if n == 1:
        axes = [axes]
    for ax, (bname, by_policy) in zip(axes, results.items()):
        truth = by_policy['Stateless']['truth']
        ax.plot(truth, 'k--', alpha=0.5, label='truth')
        for name, r in by_policy.items():
            ax.plot(r['cmds'], label=name, alpha=0.85, linewidth=1.2)
        s = by_policy['Stateless']
        t = by_policy['Temporal v1']
        c = by_policy['Composite v2']
        ax.set_title(
            f"{bname}  |  in-focus%  stateless={100*s['in_focus_frac']:.0f}  "
            f"v1={100*t['in_focus_frac']:.0f}  v2={100*c['in_focus_frac']:.0f}  "
            f"|  sweeps {s['sweeps']}->{t['sweeps']}->{c['sweeps']}",
            fontsize=9,
        )
        ax.set_ylabel('mm')
        ax.legend(fontsize=7, loc='best')
    axes[-1].set_xlabel('frame')
    fig.suptitle('PDAF AF policy comparison: Stateless vs Temporal v1 vs Composite v2',
                 fontsize=11)
    fig.tight_layout()
    out = os.path.join('out', 'v2_comparison.png')
    fig.savefig(out, dpi=130)
    print(f"\nSaved -> {out}")


if __name__ == '__main__':
    main()
