"""Latency sensitivity of config D: naive vs timestamp-compensated.

Two ways to handle a measurement that arrives N frames late:

  NAIVE        : fuse it against the CURRENT lens position. The
                 disparity was measured against where the lens was N
                 frames ago, so the implied focus target is wrong by
                 however far the lens moved since -- the policy chases
                 stale references and can oscillate.

  COMPENSATED  : fuse it against the lens position AT CAPTURE TIME
                 (timestamp-correct association). The implied target
                 is right regardless of subsequent lens motion. This
                 is the standard predictive-AF bookkeeping.

Output: out/latency_sensitivity.png  (300 seeds, mean +/- std)
"""
from __future__ import annotations
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_imx461_stats import (
    SCENES, zone_scene, aggregate_multi_zone, make_policy,
    N_QUERY_ZONES, MOTOR_MM_PER_S, WINDOW_S, TRUTH_MM,
)
from pdaf_sim.latency import LatencyBuffer
from pdaf_sim.sensor_imx461 import SIM_W_PX, SIM_H_PX, nearest_zones

N_SEEDS = 300
FPS = 60
LATENCIES = [0, 1, 2, 3]


def run_trial(seed: int, scene_kind: str, latency: int, naive: bool):
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    subj_xy = (SIM_W_PX // 2, SIM_H_PX // 2)
    zones_used = nearest_zones(subj_xy[0], subj_xy[1], N_QUERY_ZONES)
    patches = [zone_scene(z, subj_xy, scene_kind, rng) for z in zones_used]

    n_frames = int(WINDOW_S * FPS)
    max_step = MOTOR_MM_PER_S / FPS
    policy = make_policy('temporal')
    buf = LatencyBuffer(latency)

    lens = 0.0
    cmds = []
    for _ in range(n_frames):
        err_mm = lens - TRUTH_MM
        d, c, s = aggregate_multi_zone(patches, err_mm, noise_rng)
        item = buf.push_pop((d, c, lens)) if latency > 0 else (d, c, lens)
        if item is None:
            cmds.append(lens)
            continue
        d_mm, conf, lens_at_capture = item
        ref = lens if naive else lens_at_capture
        dec = policy.step(d_mm, conf, ref)
        lens += float(np.clip(dec.lens_cmd - lens, -max_step, max_step))
        cmds.append(lens)

    err = np.array(cmds) - TRUTH_MM
    return float(np.mean(np.abs(err) < 0.3)) * 100


def _worker(args):
    seed, scene, lat, naive = args
    return (scene, lat, naive, run_trial(seed, scene, lat, naive))


def main():
    os.makedirs('out', exist_ok=True)
    jobs = [(seed, scene, lat, naive)
            for seed in range(N_SEEDS)
            for scene in SCENES
            for lat in LATENCIES
            for naive in (True, False)]
    print(f"Running {len(jobs)} jobs...")
    t0 = time.time()
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(_worker, jobs, chunksize=25))
    print(f"Done in {time.time() - t0:.1f} s.")

    agg = {}
    for scene, lat, naive, v in results:
        agg.setdefault((scene, naive), {}).setdefault(lat, []).append(v)

    print(f"\nConfig D in-focus %, {N_SEEDS} seeds (naive vs compensated):")
    print(f"{'scene':<15}{'mode':<14}" + ''.join(f"lat={l}f".rjust(12) for l in LATENCIES))
    print('-' * 78)
    for scene in SCENES:
        for naive in (True, False):
            row = agg[(scene, naive)]
            mode = 'naive' if naive else 'compensated'
            cells = ''.join(
                f"{np.mean(row[l]):6.1f}+/-{np.std(row[l]):4.1f}".rjust(12)
                for l in LATENCIES)
            print(f"{scene:<15}{mode:<14}{cells}")

    plt.rcParams.update({'font.size': 12})
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True)
    for ax, scene in zip(axes, SCENES):
        for naive, style, label in ((True, 'o--', 'naive (fuse vs current lens)'),
                                    (False, 's-', 'compensated (fuse vs lens at capture)')):
            row = agg[(scene, naive)]
            means = [np.mean(row[l]) for l in LATENCIES]
            stds = [np.std(row[l]) for l in LATENCIES]
            ax.errorbar(LATENCIES, means, yerr=stds, fmt=style,
                        capsize=5, linewidth=2, markersize=8, label=label)
        ax.set_title(scene, fontsize=14, fontweight='bold')
        ax.set_xlabel('ISP latency (frames @ 60 fps)')
        ax.set_ylabel('in-focus % (err < 0.3 mm)')
        ax.set_xticks(LATENCIES)
        ax.set_ylim(-5, 105)
        ax.grid(True, alpha=0.3)
        ax.legend(loc='best', fontsize=10)
    fig.suptitle('Config D under ISP latency: timestamp-correct association '
                 'vs naive fusion  (IMX461 v3 sim, 300 seeds)',
                 fontsize=13, fontweight='bold')
    fig.tight_layout()
    out = os.path.join('out', 'latency_sensitivity.png')
    fig.savefig(out, dpi=200, bbox_inches='tight')
    print(f"\nSaved -> {out}")


if __name__ == '__main__':
    main()
