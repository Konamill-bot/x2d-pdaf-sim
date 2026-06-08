"""Fit a BehavioralPolicy to observed lens-trajectory data.

Workflow:
  1. Load a target trajectory (real-camera log OR synthetic stand-in).
  2. scipy.optimize.minimize searches policy parameters to minimize the
     gap between simulated and target trajectories across all benchmarks.
  3. Print best-fit params and save them as a JSON profile that
     run_experiment.py can load with BehavioralPolicy.from_params(...).

Until you have real GFX/A7III logs, this script ships a SYNTHETIC ground
truth generator that produces a "GFX-like" target trajectory using a
known-good parameter set. Fitting against it proves the pipeline works
end-to-end. When you do collect real data, drop the JSON in place of
the synthetic target -- nothing else changes.

Real-data format expected (JSON):
{
  "benchmark_name": "T1_static_lowcontrast",
  "frames_per_second": 60,
  "lens_mm": [...]
}
"""
from __future__ import annotations
import os
import sys
import json
import numpy as np
from scipy.optimize import differential_evolution

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.policy import BehavioralPolicy
from pdaf_sim.runner import run
from pdaf_sim.benchmarks import ALL_BENCHMARKS


# Synthetic "GFX-like" target. These params are a stand-in -- they encode
# our HYPOTHESIS about what makes GFX behave better: more aggressive trust
# in PDAF, lower sweep threshold, no state reset on shutter.
GFX_LIKE_GROUND_TRUTH = dict(
    tau_trust=0.4, tau_sweep=0.05,
    process_var=0.2, meas_var_base=0.5,
    sweep_step=0.15, snap_gain=0.7,
    reset_on_shot=False,
)


def gen_target_trajectories(bench_list, gt_params, seed=42):
    """Generate trajectories using the ground-truth policy."""
    targets = {}
    for bf in bench_list:
        bench = bf()
        policy = BehavioralPolicy.from_params(gt_params)
        result = run(bench, policy, seed=seed)
        targets[bench.name] = (bench, result['cmds'].copy())
    return targets


def eval_loss(param_vec, targets, seed=42):
    """Sum of normalized RMS error across all benchmarks."""
    p = dict(
        tau_trust=float(np.clip(param_vec[0], 0.05, 0.95)),
        tau_sweep=float(np.clip(param_vec[1], 0.01, param_vec[0] - 0.01)),
        process_var=float(np.clip(param_vec[2], 0.01, 5.0)),
        meas_var_base=float(np.clip(param_vec[3], 0.05, 10.0)),
        sweep_step=float(np.clip(param_vec[4], 0.05, 1.0)),
        snap_gain=float(np.clip(param_vec[5], 0.0, 1.0)),
        reset_on_shot=False,
    )
    total = 0.0
    for bench_name, (bench, tgt_cmds) in targets.items():
        policy = BehavioralPolicy.from_params(p)
        sim = run(bench, policy, seed=seed)
        err = sim['cmds'] - tgt_cmds
        total += float(np.sqrt(np.mean(err * err)))
    return total


def main():
    print("Generating synthetic 'GFX-like' target trajectories...")
    targets = gen_target_trajectories(ALL_BENCHMARKS, GFX_LIKE_GROUND_TRUTH)

    print("Fitting BehavioralPolicy to targets via differential evolution...")
    # bounds: tau_trust, tau_sweep, process_var, meas_var_base, sweep_step, snap_gain
    bounds = [(0.10, 0.90), (0.01, 0.30), (0.05, 2.0),
              (0.10, 3.0),  (0.05, 0.5), (0.0, 1.0)]
    res = differential_evolution(
        eval_loss, bounds, args=(targets,),
        maxiter=40, popsize=15, tol=1e-3,
        seed=0, workers=-1, polish=True, disp=True, updating='deferred',
    )

    fitted = dict(
        tau_trust=float(np.clip(res.x[0], 0.05, 0.95)),
        tau_sweep=float(np.clip(res.x[1], 0.01, res.x[0] - 0.01)),
        process_var=float(np.clip(res.x[2], 0.01, 5.0)),
        meas_var_base=float(np.clip(res.x[3], 0.05, 10.0)),
        sweep_step=float(np.clip(res.x[4], 0.05, 1.0)),
        snap_gain=float(np.clip(res.x[5], 0.0, 1.0)),
        reset_on_shot=False,
    )

    print("\n--- Ground truth vs. fitted ---")
    for k in ('tau_trust', 'tau_sweep', 'process_var', 'meas_var_base',
              'sweep_step', 'snap_gain'):
        print(f"  {k:<14}  truth={GFX_LIKE_GROUND_TRUTH[k]:.4f}   fit={fitted[k]:.4f}")
    print(f"\nFinal loss: {res.fun:.4f}")

    os.makedirs('out', exist_ok=True)
    out = os.path.join('out', 'fitted_gfxlike_profile.json')
    with open(out, 'w') as f:
        json.dump({'target': 'synthetic_gfx_like', 'params': fitted}, f, indent=2)
    print(f"Saved profile -> {out}")


if __name__ == '__main__':
    main()
