"""1000-seed lever-by-lever experiment on X2D / IMX461 geometry — v3.

Fixes over the v2 experiment (see DEV_LOG.md):

  FIX 1 (framerate): config A now actually runs at 15 fps (30 frames
    over the 2 s window) while binned configs run at 60 fps (120
    frames). Previously A and B shared identical parameters and were
    the same experiment run twice.

  FIX 2 (scene persistence): each zone's sharp patch is generated ONCE
    per trial; only photon noise is fresh per frame. Previously the
    scene texture was regenerated every frame, making measurement
    errors i.i.d. across frames — an unrealistic advantage for the
    temporal prior. With a fixed scene, systematic per-scene bias does
    NOT average out, which is the honest setting.

  FIX 3 (motor dynamics): lens motion is rate-limited per frame
    (no teleporting). XCD 55V-class LSM assumed ~18 mm/s of focus
    travel: 0.3 mm/frame at 60 fps, 1.2 mm/frame at 15 fps.

  FIX 4 (fair baseline): the stateless fallback is a monotonic
    bounded scan (reverses at travel limits), not an alternating
    +/- jitter that can never reach a distant target. The baseline
    can genuinely lock by scanning through focus — matching the real
    X2D, which usually locks (or red-boxes) within 1-2 s.

  FIX 5 (latency compensation): new config F = D + 2-frame ISP
    latency with timestamp-correct measurement association (the
    measurement is fused against the lens position AT CAPTURE TIME,
    not the current one). This is the standard predictive-AF
    bookkeeping; it converts the catastrophic naive-latency failure
    (see run_latency_sweep.py) into a non-event.

Output:
  out/imx461_full_stack_metrics.png   bar chart, 1000 seeds, mean +/- std
"""
from __future__ import annotations
import os
import sys
import time
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity, cdaf_score
from pdaf_sim.policy import Decision, TemporalPolicy, V3Policy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.latency import LatencyBuffer
from pdaf_sim.sensor_imx461 import SIM_W_PX, SIM_H_PX, NATIVE_PIX_UM, nearest_zones

# Optics
F_MM = 55.0
FNUM = 2.5
SUBJ_DIST_MM = 1500.0
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, NATIVE_PIX_UM)

# Wall-clock window simulated, seconds
WINDOW_S = 2.0

# PDAF readout (native pitch — NOT binned)
ZONE_W = 96
ZONE_H = 24
SUBJ_RADIUS_PX = 250

# Lens motor: ~18 mm/s focus-group travel (LSM-class)
MOTOR_MM_PER_S = 18.0

# AF pipeline
ISP_LATENCY_FRAMES = 2          # at 60 fps
N_QUERY_ZONES = 5

# Stats
N_SEEDS = 1000

TRUTH_MM = 2.5
LENS_LO, LENS_HI = -0.5, 6.0    # scan bounds (mm)


# ---------- fair stateless baseline: monotonic bounded scan ----------

@dataclass
class ScanStatelessPolicy:
    """Snap to PDAF when confident; otherwise scan monotonically through
    the travel range, reversing at the bounds. This is a fair CDAF-style
    fallback: it actually passes through focus instead of jittering."""
    tau: float = 0.35
    scan_step: float = 0.2
    _dir: int = 1

    def step(self, disparity: float, confidence: float, lens_pos: float) -> Decision:
        if confidence >= self.tau:
            return Decision(lens_cmd=lens_pos - disparity, swept=False)
        nxt = lens_pos + self._dir * self.scan_step
        if nxt > LENS_HI or nxt < LENS_LO:
            self._dir *= -1
            nxt = lens_pos + self._dir * self.scan_step
        return Decision(lens_cmd=nxt, swept=True)


# ---------- per-zone scene synthesis (generated ONCE per trial) ----------

def zone_scene(zone, subj_xy, scene_kind: str, rng):
    dx = zone.cx - subj_xy[0]
    dy = zone.cy - subj_xy[1]
    on_subject = float(np.sqrt(dx * dx + dy * dy)) < SUBJ_RADIUS_PX

    if scene_kind == 'low_contrast':
        patch = np.full((ZONE_H, ZONE_W), 0.5, dtype=np.float32)
        if on_subject:
            for i in range(0, ZONE_W, 3):
                patch[:, i:i + 2] += 0.08
            patch += rng.normal(0, 0.015, patch.shape)
        else:
            patch += rng.normal(0, 0.01, patch.shape)
    else:  # high_contrast
        patch = np.zeros((ZONE_H, ZONE_W), dtype=np.float32)
        n_bars, lo, hi, scale = (8, 0.4, 1.0, 0.85) if on_subject else (4, 0.2, 0.5, 0.5)
        for _ in range(n_bars):
            x = rng.integers(0, ZONE_W)
            w = rng.integers(3, 8) if on_subject else rng.integers(2, 5)
            patch[:, max(0, x - w):x + w] += rng.uniform(lo, hi)
        patch = patch / max(patch.max(), 1e-6) * scale
        patch += rng.normal(0, 0.02, patch.shape)
    return np.clip(patch, 0, 1).astype(np.float32)


def measure_zone(sharp_patch, err_mm, noise_rng):
    """Defocus + photon noise on a FIXED sharp patch (FIX 2)."""
    L, R = render_lr(sharp_patch, err_mm, F_MM, FNUM, SUBJ_DIST_MM,
                     NATIVE_PIX_UM, noise_sigma=0.01, bin_factor=1,
                     rng=noise_rng)
    disp_px, conf = estimate_disparity(L, R, max_disp_px=16)
    disp_mm = disp_px / PX_PER_MM if abs(PX_PER_MM) > 1e-9 else 0.0
    return disp_mm, conf, cdaf_score((L + R) * 0.5)


def aggregate_multi_zone(patches, err_mm, noise_rng):
    ds, cs, ss = [], [], []
    for p in patches:
        d, c, sh = measure_zone(p, err_mm, noise_rng)
        ds.append(d); cs.append(c); ss.append(sh)
    ds = np.array(ds); cs = np.array(cs)
    csum = cs.sum() + 1e-9
    d_w = float(np.sum((cs / csum) * ds))
    spread = float(np.std(ds))
    # Agreement MODULATES confidence (floor 0.5x), it must not veto
    # acquisition: at heavy defocus on weak texture, zones legitimately
    # disagree, but their weighted mean is still the best available
    # estimate. Crushing confidence to ~0 here locks the policy into a
    # permanent sweep -- the failure mode the v2 aggregation had.
    agreement = float(np.exp(-spread * 6))
    c_agg = min(1.0, float(np.mean(cs)) * (0.5 + 0.5 * agreement))
    return d_w, c_agg, float(np.mean(ss))


# ---------- configurations ----------
# (label, kind, multi_zone, latency_frames, fps)
CONFIGS = [
    ('A baseline (15fps scan)',     'stateless', False, 0, 15),
    ('B +binned AF readout (60fps)','stateless', False, 0, 60),
    ('C +Kalman',                   'temporal',  False, 0, 60),
    ('D +multi-zone (N=5)',         'temporal',  True,  0, 60),
    ('E +V3 stack',                 'v3',        True,  0, 60),
    ('F D +2f latency, compensated','temporal',  True,  ISP_LATENCY_FRAMES, 60),
]


def make_policy(kind: str):
    if kind == 'stateless':
        return ScanStatelessPolicy(tau=0.35, scan_step=0.2)
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
              use_multi_zone: bool, latency_frames: int, fps: int):
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    subj_xy = (SIM_W_PX // 2, SIM_H_PX // 2)
    zones_used = nearest_zones(subj_xy[0], subj_xy[1],
                               N_QUERY_ZONES if use_multi_zone else 1)
    # FIX 2: scene fixed per trial
    patches = [zone_scene(z, subj_xy, scene_kind, rng) for z in zones_used]

    n_frames = int(WINDOW_S * fps)
    max_step = MOTOR_MM_PER_S / fps          # FIX 3: motor rate limit

    policy = make_policy(kind)
    buf = LatencyBuffer(latency_frames)

    lens = 0.0
    cmds, sweeps = [], 0

    for _ in range(n_frames):
        err_mm = lens - TRUTH_MM
        if use_multi_zone:
            d, c, s = aggregate_multi_zone(patches, err_mm, noise_rng)
        else:
            d, c, s = measure_zone(patches[0], err_mm, noise_rng)

        # FIX 5: store lens position AT CAPTURE with the measurement, so a
        # delayed measurement is fused against the right reference.
        stale = buf.push_pop((d, c, s, lens)) if latency_frames > 0 else (d, c, s, lens)
        if stale is None:
            cmds.append(lens)
            continue
        d_mm, conf, cdaf, lens_at_capture = stale

        if kind == 'v3':
            dec = policy.step(d_mm, conf, cdaf, lens_at_capture)
        else:
            dec = policy.step(d_mm, conf, lens_at_capture)
        if dec.swept:
            sweeps += 1

        # FIX 3: motor can only move so far per frame
        lens += float(np.clip(dec.lens_cmd - lens, -max_step, max_step))
        cmds.append(lens)

    cmds = np.array(cmds)
    err = cmds - TRUTH_MM
    return dict(
        in_focus=float(np.mean(np.abs(err) < 0.3)) * 100,
        final_err=float(abs(err[-1])),
        travel=float(np.sum(np.abs(np.diff(cmds)))),
        sweeps=sweeps,
    )


def _worker(args):
    seed, scene, label, kind, mz, lat, fps = args
    r = run_trial(seed, scene, kind, mz, lat, fps)
    return (scene, label, r['in_focus'], r['final_err'], r['travel'], r['sweeps'])


SCENES = ['low_contrast', 'high_contrast']


def main():
    os.makedirs('out', exist_ok=True)
    jobs = [(seed, scene, label, kind, mz, lat, fps)
            for seed in range(N_SEEDS)
            for scene in SCENES
            for (label, kind, mz, lat, fps) in CONFIGS]

    print(f"Running {len(jobs)} jobs ({N_SEEDS} seeds x {len(SCENES)} scenes "
          f"x {len(CONFIGS)} configs)...")
    t0 = time.time()
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(_worker, jobs, chunksize=25))
    print(f"Done in {time.time() - t0:.1f} s.")

    agg = {}
    for scene, label, ifc, ferr, trv, swp in results:
        agg.setdefault(scene, {}).setdefault(label, []).append((ifc, ferr, trv, swp))

    for scene in SCENES:
        print(f"\n=== {scene} ({N_SEEDS} seeds, IMX461 v3: fixed scene, "
              f"motor-limited, fair baseline) ===")
        print(f"{'config':<34}{'in_focus%':>16}{'final_err_mm':>16}"
              f"{'travel_mm':>14}{'sweeps':>12}")
        print('-' * 95)
        for label, *_ in CONFIGS:
            a = np.array(agg[scene][label])
            print(f"{label:<34}"
                  f"{a[:,0].mean():>8.1f} +/- {a[:,0].std():>4.1f}"
                  f"{a[:,1].mean():>9.3f}+/-{a[:,1].std():>5.2f}"
                  f"{a[:,2].mean():>8.2f}+/-{a[:,2].std():>4.2f}"
                  f"{a[:,3].mean():>7.1f}+/-{a[:,3].std():>3.1f}")

    # ---------- bar chart ----------
    plt.rcParams.update({'font.size': 12})
    fig, axes = plt.subplots(1, len(SCENES), figsize=(19, 7), sharey=True)
    labels = [c[0] for c in CONFIGS]
    colors = ['C3', 'C1', 'C0', 'C4', 'C2', 'C5']
    for ax, scene in zip(axes, SCENES):
        means = [np.array(agg[scene][l])[:, 0].mean() for l in labels]
        stds = [np.array(agg[scene][l])[:, 0].std() for l in labels]
        bars = ax.bar(labels, means, yerr=stds, capsize=6, color=colors,
                      alpha=0.85, edgecolor='black', linewidth=1.2)
        ax.set_title(scene, fontsize=14, fontweight='bold')
        ax.set_ylabel('in-focus % (err < 0.3 mm)')
        ax.set_ylim(0, 115)
        ax.grid(True, axis='y', alpha=0.3)
        for tick in ax.get_xticklabels():
            tick.set_rotation(20)
            tick.set_horizontalalignment('right')
            tick.set_fontsize(10)
        for bar, m, s in zip(bars, means, stds):
            ax.text(bar.get_x() + bar.get_width() / 2, m + s + 2,
                    f'{m:.0f}±{s:.0f}', ha='center', va='bottom',
                    fontsize=11, fontweight='bold')
    fig.suptitle(
        f'IMX461 sim v3: 294-zone PDAF, native pitch, fixed scene per trial, '
        f'motor-limited, fair scan baseline | {N_SEEDS} seeds, mean ± std',
        fontsize=13, fontweight='bold')
    fig.tight_layout()
    out1 = os.path.join('out', 'imx461_full_stack_metrics.png')
    fig.savefig(out1, dpi=200, bbox_inches='tight')
    print(f"\nSaved -> {out1}")


if __name__ == '__main__':
    main()
