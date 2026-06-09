"""Sensitivity of config D in-focus % to ISP latency in {0, 1, 2, 3} frames.

Uses the same IMX461 294-zone simulator as run_imx461_stats.py but
sweeps the latency parameter. Important caveat: this uses NAIVE
latency buffering -- the policy is not predict-forward compensated.
Real implementations would compensate. The point of this experiment
is to show how badly uncompensated latency degrades performance, NOT
to suggest performance under properly-compensated latency.

Output: out/latency_sensitivity.png
"""
from __future__ import annotations
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.policy import TemporalPolicy
from pdaf_sim.latency import LatencyBuffer
from pdaf_sim.sensor_imx461 import SIM_W_PX, SIM_H_PX, nearest_zones

# Import shared bits from the IMX461 stats script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_imx461_stats import (
    SCENES, aggregate_multi_zone, N_QUERY_ZONES, N_FRAMES, make_policy,
)

LATENCY_FRAMES = [0, 1, 2, 3]
N_SEEDS = 300


def run_trial(seed, scene_kind, latency_frames):
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    subj_xy = (SIM_W_PX // 2, SIM_H_PX // 2)
    zones_used = nearest_zones(subj_xy[0], subj_xy[1], N_QUERY_ZONES)
    policy = make_policy('temporal')
    buf = LatencyBuffer(latency_frames)
    truth = 2.5
    lens = 0.0
    cmds = []
    for k in range(N_FRAMES):
        err_mm = lens - truth
        meas = aggregate_multi_zone(zones_used, subj_xy, scene_kind,
                                    err_mm, rng, noise_rng)
        stale = buf.push_pop(meas) if latency_frames > 0 else meas
        if stale is None:
            cmds.append(lens); continue
        d_mm, conf, _ = stale
        dec = policy.step(d_mm, conf, lens)
        lens = dec.lens_cmd
        cmds.append(lens)
    cmds = np.array(cmds)
    err = cmds - truth
    return float(np.mean(np.abs(err) < 0.3)) * 100


def _worker(args):
    seed, scene, lat = args
    return (scene, lat, run_trial(seed, scene, lat))


def main():
    os.makedirs('out', exist_ok=True)
    jobs = [(s, sc, l) for s in range(N_SEEDS) for sc in SCENES
            for l in LATENCY_FRAMES]
    print(f"Running {len(jobs)} jobs ({N_SEEDS} seeds x {len(SCENES)} scenes "
          f"x {len(LATENCY_FRAMES)} latency values)...")
    t0 = time.time()
    with ProcessPoolExecutor() as pool:
        out = list(pool.map(_worker, jobs, chunksize=10))
    print(f"Done in {time.time() - t0:.1f} s.")

    agg = {}
    for sc, lat, ifc in out:
        agg.setdefault(sc, {}).setdefault(lat, []).append(ifc)

    print(f"\nNaive-latency sensitivity of config D ({N_SEEDS} seeds, mean +/- std):\n")
    print(f"{'scene':<16}" + "".join(f"{f'lat={l}f':>14}" for l in LATENCY_FRAMES))
    print('-' * 70)
    for sc in SCENES:
        row = sc.ljust(16)
        for l in LATENCY_FRAMES:
            arr = np.array(agg[sc][l])
            row += f"{arr.mean():>5.1f} +/- {arr.std():>4.1f}"
        print(row)

    # Plot
    plt.rcParams.update({'font.size': 12})
    fig, ax = plt.subplots(figsize=(11, 6))
    x = np.arange(len(LATENCY_FRAMES))
    w = 0.35
    for i, sc in enumerate(SCENES):
        means = [np.mean(agg[sc][l]) for l in LATENCY_FRAMES]
        stds = [np.std(agg[sc][l]) for l in LATENCY_FRAMES]
        ax.bar(x + (i - 0.5) * w, means, w, yerr=stds, capsize=5,
               label=sc, alpha=0.85, edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels([f"{l} frame ({l*16.7:.0f} ms)" for l in LATENCY_FRAMES])
    ax.set_ylabel('in-focus % (err < 0.3 mm)', fontsize=12)
    ax.set_xlabel('Naive ISP latency (no predict-forward compensation)',
                  fontsize=12)
    ax.set_ylim(0, 110)
    ax.set_title(
        'Config D sensitivity to UNCOMPENSATED ISP latency\n'
        '(294-zone IMX461 sim, 300 seeds — compensated latency would recover this)',
        fontsize=13, fontweight='bold')
    ax.legend(fontsize=11)
    ax.grid(True, axis='y', alpha=0.3)
    fig.tight_layout()
    out_path = os.path.join('out', 'latency_sensitivity.png')
    fig.savefig(out_path, dpi=200, bbox_inches='tight')
    print(f"\nSaved -> {out_path}")


if __name__ == '__main__':
    main()
