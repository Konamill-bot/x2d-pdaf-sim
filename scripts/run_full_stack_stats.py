"""Full-stack experiment with multi-seed statistics + clean plots.

Same 5 lever configurations as run_full_stack.py, but:
- 10 seeds per config (different random scenes + noise)
- Report mean +/- std of metrics
- Two clean output figures:
    out/full_stack_metrics.png : bar chart of in_focus_pct with error bars
    out/full_stack_trajectory.png : single representative seed trajectory
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
N_SEEDS = 10


def fresh_policy(p):
    """Return a fresh instance of the same policy type with same hyperparams."""
    cls = type(p)
    kw = {k: v for k, v in p.__dict__.items()
          if not k.startswith('_') and not callable(v)
          and not isinstance(v, (np.ndarray,))}
    return cls(**kw)


def run(scene_name: str, *, bin_factor: int, policy_proto,
        use_multi_zone: bool, uses_cdaf: bool, seed: int):
    fps = BIN_TO_FPS[bin_factor]
    n_frames = int(HORIZON_SECONDS * fps)
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    sharp = SCENES[scene_name](rng)
    truth = np.full(n_frames, 2.5)
    lens_mm = 0.0
    policy = fresh_policy(policy_proto)
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
    return {
        't': t, 'cmds': cmds,
        'sweeps': sweeps,
        't_focus_s': t_focus,
        'final_err_mm': float(abs(cmds[-1] - 2.5)),
        'in_focus_pct': 100 * float(in_focus.mean()),
        'travel_mm': float(np.sum(np.abs(np.diff(cmds)))),
    }


def build_configs():
    return [
        ('A baseline',
         dict(bin_factor=1, policy_proto=StatelessPolicy(tau=0.35, sweep_step=0.2),
              use_multi_zone=False, uses_cdaf=False)),
        ('B +binning',
         dict(bin_factor=4, policy_proto=StatelessPolicy(tau=0.35, sweep_step=0.2),
              use_multi_zone=False, uses_cdaf=False)),
        ('C +Kalman',
         dict(bin_factor=4,
              policy_proto=TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                                          process_var=0.5*15/60, meas_var_base=1.0),
              use_multi_zone=False, uses_cdaf=False)),
        ('D +multi-zone',
         dict(bin_factor=4,
              policy_proto=TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                                          process_var=0.5*15/60, meas_var_base=1.0),
              use_multi_zone=True, uses_cdaf=False)),
        ('E +deadband+PID+CDAF',
         dict(bin_factor=4, policy_proto=V3Policy(),
              use_multi_zone=True, uses_cdaf=True)),
    ]


def main():
    os.makedirs('out', exist_ok=True)
    scenes = ['low_contrast', 'high_contrast']
    configs = build_configs()

    # results[scene][config_name] = list of N_SEEDS dicts
    results = {s: {} for s in scenes}
    for scene in scenes:
        for label, kwargs in configs:
            seed_runs = [run(scene, seed=s, **kwargs) for s in range(N_SEEDS)]
            results[scene][label] = seed_runs

    # ---------- print summary ----------
    for scene in scenes:
        print(f"\n=== {scene} (mean +/- std over {N_SEEDS} seeds) ===")
        print(f"{'config':<24}{'in_focus_pct':>18}{'t_focus_s':>16}"
              f"{'final_err_mm':>16}{'travel_mm':>14}{'sweeps':>10}")
        print('-' * 100)
        for label, _ in configs:
            rs = results[scene][label]
            def mstd(key):
                vals = np.array([r[key] for r in rs], dtype=float)
                vals = vals[~np.isnan(vals)]
                if len(vals) == 0:
                    return float('nan'), float('nan')
                return float(np.mean(vals)), float(np.std(vals))
            inf_m, inf_s = mstd('in_focus_pct')
            tf_m,  tf_s  = mstd('t_focus_s')
            err_m, err_s = mstd('final_err_mm')
            trv_m, trv_s = mstd('travel_mm')
            sw_m,  sw_s  = mstd('sweeps')
            tf_str = (f'{tf_m:.3f}+/-{tf_s:.3f}' if not np.isnan(tf_m) else 'no lock')
            print(f"{label:<24}{f'{inf_m:5.1f} +/- {inf_s:4.1f}':>18}"
                  f"{tf_str:>16}{f'{err_m:.3f}+/-{err_s:.3f}':>16}"
                  f"{f'{trv_m:.2f}+/-{trv_s:.2f}':>14}"
                  f"{f'{sw_m:.0f}+/-{sw_s:.0f}':>10}")

    # ---------- plot 1: bar chart with error bars ----------
    fig, axes = plt.subplots(1, len(scenes), figsize=(12, 5), sharey=True)
    config_names = [label for label, _ in configs]
    colors = ['C3', 'C1', 'C0', 'C4', 'C2']
    for ax, scene in zip(axes, scenes):
        means = []
        stds = []
        for label, _ in configs:
            vals = np.array([r['in_focus_pct'] for r in results[scene][label]])
            means.append(np.mean(vals))
            stds.append(np.std(vals))
        bars = ax.bar(config_names, means, yerr=stds, capsize=4,
                      color=colors, alpha=0.85, edgecolor='black')
        ax.set_title(scene, fontsize=12)
        ax.set_ylabel('in-focus % (err < 0.3 mm)')
        ax.set_ylim(0, 105)
        ax.grid(True, axis='y', alpha=0.3)
        for tick in ax.get_xticklabels():
            tick.set_rotation(20)
            tick.set_horizontalalignment('right')
        for bar, m, s in zip(bars, means, stds):
            ax.text(bar.get_x() + bar.get_width() / 2, m + s + 2,
                    f'{m:.0f}±{s:.0f}', ha='center', va='bottom', fontsize=9)
    fig.suptitle(f'Lever-by-lever in-focus performance ({N_SEEDS} seeds, mean ± std)',
                 fontsize=12)
    fig.tight_layout()
    out1 = os.path.join('out', 'full_stack_metrics.png')
    fig.savefig(out1, dpi=130)
    print(f"\nSaved -> {out1}")

    # ---------- plot 2: representative trajectories (seed 0) ----------
    fig, axes = plt.subplots(len(scenes), 1, figsize=(11, 4*len(scenes)),
                             sharex=True)
    if len(scenes) == 1:
        axes = [axes]
    for ax, scene in zip(axes, scenes):
        ax.axhline(2.5, color='k', linestyle='--', alpha=0.5, label='truth = 2.5 mm')
        for (label, _), c in zip(configs, colors):
            r = results[scene][label][0]
            tf_str = (f'lock {r["t_focus_s"]:.2f}s' if not np.isnan(r["t_focus_s"])
                      else 'no lock')
            ax.plot(r['t'], r['cmds'], color=c, linewidth=1.6, alpha=0.9,
                    label=f"{label}  ({tf_str}, err {r['final_err_mm']:.2f}mm)")
        ax.set_title(scene, fontsize=11)
        ax.set_ylabel('lens position (mm)')
        ax.legend(loc='best', fontsize=9)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel('time (s)')
    fig.suptitle('Representative trajectory (seed 0) for each lever stack',
                 fontsize=12)
    fig.tight_layout()
    out2 = os.path.join('out', 'full_stack_trajectory.png')
    fig.savefig(out2, dpi=130)
    print(f"Saved -> {out2}")


if __name__ == '__main__':
    main()
