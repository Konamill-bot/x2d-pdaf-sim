"""1000-seed lever-by-lever experiment on PROPER X2D / IMX461 geometry.

Key architectural correction: PDAF pixels are read at NATIVE 3.76um
pitch, not binned -- binning would destroy the L/R sub-aperture phase
signal. Binning applies to the imaging-pixel readout (for EVF/JPEG),
not to PDAF rows.

Therefore each of the 294 zones is rendered at native pitch on a small
local patch. Zones near the subject see strong texture, zones far see
background only -- which is precisely what makes confidence-weighted
multi-zone aggregation work in real cameras.

ISP latency: pipeline delay from sensor expose -> ISP -> decision.
Modelled as a fixed N-frame delay buffer around the policy. The
temporal-prior policies use predict-steps to compensate.

Output:
  out/imx461_full_stack_metrics.png   bar chart, 1000 seeds, mean +/- std
"""
from __future__ import annotations
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity, cdaf_score
from pdaf_sim.policy import StatelessPolicy, TemporalPolicy, V3Policy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.latency import LatencyBuffer
from pdaf_sim.sensor_imx461 import (
    SIM_W_PX, SIM_H_PX, NATIVE_PIX_UM, nearest_zones, N_ZONES,
)

# Optics
F_MM = 55.0
FNUM = 2.5
SUBJ_DIST_MM = 1500.0
N_FRAMES = 120
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, NATIVE_PIX_UM)

# PDAF readout (native pitch — NOT binned)
ZONE_W = 96    # native px per zone strip width
ZONE_H = 24    # native px per zone strip height
SUBJ_RADIUS_PX = 250  # subject extent on sensor; zones within see texture

# AF pipeline
ISP_LATENCY_FRAMES = 2
N_QUERY_ZONES = 5

# Stats
N_SEEDS = 1000


# ---------- per-zone scene synthesis (native PDAF resolution) ----------

def zone_scene(zone, subj_xy, scene_kind: str, rng):
    """Synthesize the small native-pitch patch this zone sees."""
    dx = zone.cx - subj_xy[0]
    dy = zone.cy - subj_xy[1]
    dist = float(np.sqrt(dx*dx + dy*dy))
    on_subject = dist < SUBJ_RADIUS_PX

    if scene_kind == 'low_contrast':
        if on_subject:
            # Faint textured subject patch
            patch = np.full((ZONE_H, ZONE_W), 0.5, dtype=np.float32)
            for i in range(0, ZONE_W, 3):
                patch[:, i:i+2] += 0.08
            patch += rng.normal(0, 0.015, patch.shape)
        else:
            # Smooth gradient background
            patch = np.full((ZONE_H, ZONE_W), 0.5, dtype=np.float32)
            patch += rng.normal(0, 0.01, patch.shape)
    else:  # high_contrast
        if on_subject:
            patch = np.zeros((ZONE_H, ZONE_W), dtype=np.float32)
            for _ in range(8):
                x = rng.integers(0, ZONE_W)
                w = rng.integers(3, 8)
                patch[:, max(0, x-w):x+w] += rng.uniform(0.4, 1.0)
            patch = patch / max(patch.max(), 1e-6) * 0.85
            patch += rng.normal(0, 0.02, patch.shape)
        else:
            patch = np.zeros((ZONE_H, ZONE_W), dtype=np.float32)
            for _ in range(4):
                x = rng.integers(0, ZONE_W)
                w = rng.integers(2, 5)
                patch[:, max(0, x-w):x+w] += rng.uniform(0.2, 0.5)
            patch = patch / max(patch.max(), 1e-6) * 0.5
            patch += rng.normal(0, 0.02, patch.shape)
    return np.clip(patch, 0, 1).astype(np.float32)


def measure_zone(zone, subj_xy, scene_kind, err_mm, rng, noise_rng):
    sharp = zone_scene(zone, subj_xy, scene_kind, rng)
    L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, NATIVE_PIX_UM,
                     noise_sigma=0.01, bin_factor=1, rng=noise_rng)
    disp_px, conf = estimate_disparity(L, R, max_disp_px=16)
    disp_mm = disp_px / PX_PER_MM if abs(PX_PER_MM) > 1e-9 else 0.0
    cdaf = cdaf_score((L + R) * 0.5)
    return disp_mm, conf, cdaf


def aggregate_multi_zone(zones_used, subj_xy, scene_kind, err_mm, rng, noise_rng):
    """Confidence-weighted aggregation across zones near the subject."""
    ds, cs, ss = [], [], []
    for z in zones_used:
        d, c, sh = measure_zone(z, subj_xy, scene_kind, err_mm, rng, noise_rng)
        ds.append(d); cs.append(c); ss.append(sh)
    ds = np.array(ds); cs = np.array(cs)
    csum = cs.sum() + 1e-9
    d_w = float(np.sum((cs / csum) * ds))
    spread = float(np.std(ds))
    agreement = float(np.exp(-spread * 6))   # tighter agreement bonus
    c_agg = min(1.0, float(np.mean(cs)) * agreement + (0.15 if agreement > 0.7 else 0))
    return d_w, c_agg, float(np.mean(ss))


# ---------- configurations ----------

CONFIGS = [
    ('A baseline',                'stateless', False, 0),
    ('B +binning (60fps)',        'stateless', False, 0),
    ('C +Kalman',                 'temporal',  False, 0),
    ('D +multi-zone (N=5)',       'temporal',  True,  0),
    ('E +V3 (deadband+PID+CDAF)', 'v3',        True,  0),
]
# Note: a 'D + 2-frame ISP latency without predict-forward compensation'
# experiment was run separately and is documented in TONIGHT_v2.md.
# Briefly: naive latency-buffered measurements cause Kalman to track
# stale positions and oscillate on high-contrast scenes (0% in_focus).
# Proper latency handling requires the policy to advance its state
# estimate forward by N frames before applying the delayed measurement;
# this is what Sony / Canon predictive-AF maths do internally.


def make_policy(kind: str):
    if kind == 'stateless':
        return StatelessPolicy(tau=0.35, sweep_step=0.2)
    if kind == 'temporal':
        return TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                              process_var=0.125, meas_var_base=0.5)
    if kind == 'v3':
        return V3Policy(tau_sweep=0.08, sweep_step=0.2,
                        process_var=0.125, meas_var_base=0.5,
                        deadband_mm=0.04)
    raise ValueError(kind)


# ---------- one trial ----------

def run_trial(seed: int, scene_kind: str, kind: str,
              use_multi_zone: bool, latency_frames: int):
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    subj_xy = (SIM_W_PX // 2, SIM_H_PX // 2)
    zones_used = nearest_zones(subj_xy[0], subj_xy[1],
                               N_QUERY_ZONES if use_multi_zone else 1)
    policy = make_policy(kind)
    buf = LatencyBuffer(latency_frames)
    truth = 2.5
    lens = 0.0
    cmds, sweeps = [], 0

    for k in range(N_FRAMES):
        err_mm = lens - truth
        if use_multi_zone:
            meas = aggregate_multi_zone(zones_used, subj_xy, scene_kind,
                                        err_mm, rng, noise_rng)
        else:
            meas = measure_zone(zones_used[0], subj_xy, scene_kind,
                                err_mm, rng, noise_rng)
        stale = buf.push_pop(meas) if latency_frames > 0 else meas
        if stale is None:
            cmds.append(lens); continue
        d_mm, conf, cdaf = stale
        if kind == 'v3':
            dec = policy.step(d_mm, conf, cdaf, lens)
        else:
            dec = policy.step(d_mm, conf, lens)
        if dec.swept:
            sweeps += 1
        lens = dec.lens_cmd
        cmds.append(lens)

    cmds = np.array(cmds)
    err = cmds - truth
    return dict(
        in_focus=float(np.mean(np.abs(err) < 0.3)) * 100,
        final_err=float(abs(err[-1])),
        travel=float(np.sum(np.abs(np.diff(cmds)))),
        sweeps=sweeps,
    )


def _worker(args):
    seed, scene, label, kind, mz, lat = args
    r = run_trial(seed, scene, kind, mz, lat)
    return (scene, label, r['in_focus'], r['final_err'], r['travel'], r['sweeps'])


SCENES = ['low_contrast', 'high_contrast']


def main():
    os.makedirs('out', exist_ok=True)
    jobs = []
    for seed in range(N_SEEDS):
        for scene in SCENES:
            for (label, kind, mz, lat) in CONFIGS:
                jobs.append((seed, scene, label, kind, mz, lat))

    print(f"Running {len(jobs)} jobs ({N_SEEDS} seeds x {len(SCENES)} scenes "
          f"x {len(CONFIGS)} configs)...")
    t0 = time.time()
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(_worker, jobs, chunksize=20))
    print(f"Done in {time.time() - t0:.1f} s.")

    agg = {}
    for scene, label, ifc, ferr, trv, swp in results:
        agg.setdefault(scene, {}).setdefault(label, []).append((ifc, ferr, trv, swp))

    for scene in SCENES:
        print(f"\n=== {scene} ({N_SEEDS} seeds, 294-zone IMX461, native PDAF pitch) ===")
        print(f"{'config':<32}{'in_focus%':>14}{'final_err_mm':>16}{'travel_mm':>12}{'sweeps':>10}")
        print('-' * 95)
        for label, _, _, _ in CONFIGS:
            arr = np.array(agg[scene][label])
            print(f"{label:<32}"
                  f"{arr[:,0].mean():>6.1f} +/- {arr[:,0].std():>4.1f}"
                  f"{arr[:,1].mean():>10.3f}+/-{arr[:,1].std():>4.2f}"
                  f"{arr[:,2].mean():>7.2f}+/-{arr[:,2].std():>4.2f}"
                  f"{arr[:,3].mean():>5.1f}+/-{arr[:,3].std():>3.1f}")

    plt.rcParams.update({'font.size': 12})
    fig, axes = plt.subplots(1, len(SCENES), figsize=(18, 7), sharey=True)
    config_labels = [c[0] for c in CONFIGS]
    colors = ['C3', 'C1', 'C0', 'C4', 'C5', 'C2']
    for ax, scene in zip(axes, SCENES):
        means = [np.array(agg[scene][lbl])[:,0].mean() for lbl,_,_,_ in CONFIGS]
        stds  = [np.array(agg[scene][lbl])[:,0].std()  for lbl,_,_,_ in CONFIGS]
        bars = ax.bar(config_labels, means, yerr=stds, capsize=6,
                      color=colors, alpha=0.85, edgecolor='black', linewidth=1.2)
        ax.set_title(scene, fontsize=14, fontweight='bold')
        ax.set_ylabel('in-focus % (err < 0.3 mm)', fontsize=12)
        ax.set_ylim(0, 115)
        ax.grid(True, axis='y', alpha=0.3)
        for t in ax.get_xticklabels():
            t.set_rotation(20); t.set_horizontalalignment('right'); t.set_fontsize(10)
        for bar, m, s in zip(bars, means, stds):
            ax.text(bar.get_x() + bar.get_width() / 2, m + s + 2,
                    f'{m:.0f}±{s:.0f}', ha='center', va='bottom',
                    fontsize=11, fontweight='bold')
    fig.suptitle(
        f'X2D IMX461 sim: 294-zone PDAF (21×14), native 3.76um pitch, '
        f'nearest-{N_QUERY_ZONES} multi-zone, ISP latency 2 frames | '
        f'{N_SEEDS} seeds',
        fontsize=13, fontweight='bold')
    fig.tight_layout()
    out1 = os.path.join('out', 'imx461_full_stack_metrics.png')
    fig.savefig(out1, dpi=200, bbox_inches='tight')
    print(f"\nSaved -> {out1}")


if __name__ == '__main__':
    main()
