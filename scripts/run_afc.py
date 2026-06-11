"""Continuous-AF (AF-C) feasibility study on X2D / IMX461 geometry -- v3.

The 1000-seed study (run_imx461_stats.py) tests AF-S: stationary
subject, lens converges. This script tests the case Hasselblad says
the X2D cannot do -- AF-C: the subject MOVES in depth and the policy
must track a moving focus target.

No new machinery: TemporalPolicy's Kalman state is already
(focus_position, focus_VELOCITY). That velocity term IS predictive
continuous AF. We point the existing stack at moving subjects.

v2 hardening (after honest review of v1 -- see DEV_LOG.md Day 4):

  H1 (dropout grid, not a chosen point): v1 used one hand-picked
     dropout (P=0.30, conf=0.15) that happened to sit between AF-S's
     trust threshold and AF-C's sweep floor -- constructing the result.
     v2 sweeps the full P_DROPOUT x DROPOUT_CONF grid, INCLUDING the
     regions where AF-C's advantage disappears (conf above AF-S's
     threshold: neither hunts; conf below AF-C's sweep floor: both
     hunt). Showing the boundary is the honest version of the claim.

  H2 (erratic motion): constant-velocity walk is the CV-Kalman's best
     case. v2 adds an erratic scenario -- approach, HARD STOP, reverse.
     A velocity model coasts PAST a hard stop; we measure that
     overshoot rather than hiding it (worst_err metric).

  H3 (100MP-grade focus criterion): "in focus" at |err| < 0.3 mm is a
     visible-sharpness band. v2 also scores a pixel-level band: the
     defocus at which the CoC radius reaches 1 native pixel (3.76 um).
     (Computed: 0.494 mm -- LOOSER than the 0.3 mm band, i.e. the
     original criterion was already at-or-tighter than pixel level.)

  H4 (burst blackout): real AF-C must survive shot-to-shot PDAF
     blackouts during burst (exposure + readout windows where no PDAF
     arrives). Periodic blind windows at ~3.3 fps burst cadence.
     During a blind frame the stateless policy can only HOLD; the
     Kalman policy COASTS on its velocity state.

v3 hardening (firmware-evidence fit):

  H5 (AF-loop-rate sweep): the 60 fps loop rate was the study's
     largest unverifiable assumption. The externally checkable facts
     are narrower:
       - X2D firmware 3.1.0 (2023-11-30) added face detection in AF
         mode -- proof that SOME continuous per-frame computation runs
         on the live stream (hasselblad.com release notes).
       - The EVF runs at 60 fps from a subsampled readout, so the
         sensor side sustains a 60 Hz stream (FINDINGS.md).
       - The face-detector's own inference rate is NOT published.
         On mobile-class ISPs detectors commonly run at 1/2 or 1/4
         of stream rate (15-30 Hz) with box interpolation between.
     So instead of assuming 60 fps, v3 sweeps the AF measurement
     loop at 15 / 30 / 60 fps. Motor step, pipeline latency
     (modelled as ~33 ms of WALL TIME, converted to frames at each
     rate), acquisition grace, and burst cadence all scale with the
     rate; subject motion stays defined in real time (mm/s). If AF-C
     survives at 15 fps, the conclusion no longer depends on the
     unverifiable 60 fps assumption at all.

Honest scope note: this demonstrates the DECISION-ALGORITHM half of
AF-C on this sensor geometry. It cannot prove from outside what loop
rate the X2D ISP actually sustains -- the rate sweep brackets the
question instead of assuming it away. The LATERAL half of AF-C
(subject recognition, zone hand-off across the frame) is deliberately
out of scope (FINDINGS.md Layer 3).

Outputs:
  out/afc_tracking.png       walk + erratic trajectories (60 fps, one seed)
  out/afc_dropout_grid.png   track% heatmap over the dropout grid
  out/afc_burst.png          burst-blackout trajectory (one seed)
  out/afc_fps_sweep.png      track% vs AF loop rate (H5)
  console                    multi-seed tables, mean +/- std
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
from pdaf_sim.phase_corr import estimate_disparity
from pdaf_sim.policy import Decision, TemporalPolicy
from pdaf_sim.psf import signed_disparity_px, coc_radius_px
from pdaf_sim.latency import LatencyBuffer
from pdaf_sim.sensor_imx461 import SIM_W_PX, SIM_H_PX, NATIVE_PIX_UM, nearest_zones

# Optics (XCD 55V on IMX461) -- identical to the AF-S study.
F_MM = 55.0
FNUM = 2.5
SUBJ_DIST_MM = 1500.0
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, NATIVE_PIX_UM)

WINDOW_S = 2.0

ZONE_W = 96
ZONE_H = 24
SUBJ_RADIUS_PX = 250

MOTOR_MM_PER_S = 18.0

# Pipeline latency in WALL TIME (sensor expose -> disparity at policy).
# ~2 frames at 60 fps; converted to frames at each loop rate (H5).
ISP_LATENCY_MS = 33.0
N_QUERY_ZONES = 5

LENS_LO, LENS_HI = -0.5, 6.0
ACQUIRE_GRACE_S = 0.33

# H3: focus bands. BAND_VISIBLE is the AF-S study's criterion.
# BAND_PIXEL = defocus where CoC radius reaches 1 native pixel.
BAND_VISIBLE = 0.3
_K_COC = coc_radius_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, NATIVE_PIX_UM)
BAND_PIXEL = 1.0 / _K_COC if _K_COC > 1e-9 else BAND_VISIBLE

# Default dropout for scenario/burst/fps experiments (the grid sweeps it).
P_DROPOUT = 0.30
DROPOUT_CONF = 0.15

# H4: burst cadence ~3.3 fps, in wall time. Blind = exposure + readout.
BURST_START_S = 0.5
BURST_PERIOD_S = 0.30
BLACKOUT_S = 0.12

# H5: AF measurement loop rates to sweep. 60 = EVF stream rate
# (verified); 30 / 15 = detector-style subrates (1/2, 1/4 of stream),
# the conservative readings of what FW 3.1 face detection proves.
FPS_BASE = 60
FPS_SWEEP = [15, 30, 60]

N_SEEDS = 300
N_SEEDS_GRID = 60
N_SEEDS_FPS = 200

# H1: dropout grid. Spans BOTH boundaries on purpose:
#   conf 0.45 > AF-S trust threshold (0.35): dropout invisible to both.
#   conf 0.02 < AF-C sweep floor (0.08): both policies sweep.
GRID_P = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
GRID_CONF = [0.02, 0.06, 0.12, 0.20, 0.30, 0.45]


# ---------- subject motion models (defined in real time, seconds) ----------

def truth_walk(t: float) -> float:
    """Constant-velocity approach: 1.2 -> 4.4 mm over the window."""
    return 1.2 + 3.2 * (t / WINDOW_S)


def truth_erratic(t: float) -> float:
    """H2: approach fast, HARD STOP, then reverse. Two velocity
    discontinuities -- the worst case for a constant-velocity prior."""
    if t < 0.8:
        return 1.2 + 2.75 * t            # approach: 1.2 -> 3.4
    if t < 1.2:
        return 3.4                       # hard stop, holds 0.4 s
    return 3.4 - 1.75 * (t - 1.2)        # reverse: 3.4 -> 2.0


SCENARIOS = {'walk': truth_walk, 'erratic': truth_erratic}


# ---------- policies ----------

@dataclass
class StatelessAFS:
    """Snap-to-PDAF above a single trust threshold; monotonic bounded
    scan below it. No velocity model: during a PDAF blackout it can
    only hold position."""
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

    def coast(self, lens_pos: float) -> Decision:
        return Decision(lens_cmd=lens_pos, swept=False)


def make_policy(kind: str, latency_frames: int):
    if kind == 'afs':
        return StatelessAFS(tau=0.35, scan_step=0.2)
    if kind == 'afc':
        return TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                              process_var=0.05, meas_var_base=0.5,
                              predict_frames=latency_frames + 1)
    raise ValueError(kind)


# ---------- scene + measurement (same as AF-S study) ----------

def zone_scene(zone, subj_xy, rng):
    dx = zone.cx - subj_xy[0]
    dy = zone.cy - subj_xy[1]
    on_subject = float(np.sqrt(dx * dx + dy * dy)) < SUBJ_RADIUS_PX
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
    L, R = render_lr(sharp_patch, err_mm, F_MM, FNUM, SUBJ_DIST_MM,
                     NATIVE_PIX_UM, noise_sigma=0.01, bin_factor=1,
                     rng=noise_rng)
    disp_px, conf = estimate_disparity(L, R, max_disp_px=16)
    disp_mm = disp_px / PX_PER_MM if abs(PX_PER_MM) > 1e-9 else 0.0
    return disp_mm, conf


def aggregate_multi_zone(patches, err_mm, noise_rng):
    ds, cs = [], []
    for p in patches:
        d, c = measure_zone(p, err_mm, noise_rng)
        ds.append(d); cs.append(c)
    ds = np.array(ds); cs = np.array(cs)
    csum = cs.sum() + 1e-9
    d_w = float(np.sum((cs / csum) * ds))
    spread = float(np.std(ds))
    agreement = float(np.exp(-spread * 6))
    c_agg = min(1.0, float(np.mean(cs)) * (0.5 + 0.5 * agreement))
    return d_w, c_agg


# ---------- trial ----------

def run_trial(seed: int, kind: str, lat_ms: float, scenario: str,
              p_drop: float, drop_conf: float, burst: bool,
              fps: int, want_trace: bool = False):
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    drop_rng = np.random.default_rng(seed + 200)
    truth_fn = SCENARIOS[scenario]

    n_frames = int(WINDOW_S * fps)
    max_step = MOTOR_MM_PER_S / fps
    latency_frames = int(round(lat_ms / 1000.0 * fps))
    grace = int(ACQUIRE_GRACE_S * fps)

    def is_blackout(k: int) -> bool:
        t = k / fps
        if t < BURST_START_S:
            return False
        return ((t - BURST_START_S) % BURST_PERIOD_S) < BLACKOUT_S

    subj_xy = (SIM_W_PX // 2, SIM_H_PX // 2)
    zones_used = nearest_zones(subj_xy[0], subj_xy[1], N_QUERY_ZONES)
    patches = [zone_scene(z, subj_xy, rng) for z in zones_used]

    policy = make_policy(kind, latency_frames)
    buf = LatencyBuffer(latency_frames)
    lens = truth_fn(0.0) - 1.0
    cmds, truths = [], []
    hunts = 0

    for k in range(n_frames):
        tk = truth_fn(k / fps)

        if burst and is_blackout(k):
            # H4: no PDAF this frame. Stateless holds; Kalman coasts.
            dec = policy.coast(lens) if kind == 'afs' else policy.coast()
            lens += float(np.clip(dec.lens_cmd - lens, -max_step, max_step))
            cmds.append(lens); truths.append(tk)
            continue

        err_mm = lens - tk
        d, c = aggregate_multi_zone(patches, err_mm, noise_rng)
        if drop_rng.random() < p_drop:
            c = min(c, drop_conf)

        stale = buf.push_pop((d, c, lens)) if latency_frames > 0 else (d, c, lens)
        if stale is None:
            cmds.append(lens); truths.append(tk)
            continue
        d_mm, conf, lens_at_capture = stale

        dec = policy.step(d_mm, conf, lens_at_capture)
        if dec.swept:
            hunts += 1
        lens += float(np.clip(dec.lens_cmd - lens, -max_step, max_step))
        cmds.append(lens); truths.append(tk)

    cmds = np.array(cmds); truths = np.array(truths)
    err = np.abs(cmds - truths)[grace:]
    res = dict(
        track_vis=float(np.mean(err < BAND_VISIBLE)) * 100,
        track_pix=float(np.mean(err < BAND_PIXEL)) * 100,
        mean_lag=float(np.mean(err)),
        worst_err=float(np.max(err)),
        hunts=hunts,
    )
    if want_trace:
        res['cmds'] = cmds
        res['truths'] = truths
    return res


def _worker(args):
    key, seed, kind, lat_ms, scenario, p_drop, drop_conf, burst, fps = args
    r = run_trial(seed, kind, lat_ms, scenario, p_drop, drop_conf, burst, fps)
    return (key, r['track_vis'], r['track_pix'], r['mean_lag'],
            r['worst_err'], r['hunts'])


# ---------- experiment definitions ----------

CONFIGS = [
    ('AF-S',          'afs', 0.0),
    ('AF-C',          'afc', 0.0),
    ('AF-C +33ms lat', 'afc', ISP_LATENCY_MS),
]


def print_table(title, agg, keys):
    print(f"\n=== {title} ===")
    print(f"{'config':<24}{'track% (0.3mm)':>16}{'track% (1px CoC)':>18}"
          f"{'mean_lag_mm':>14}{'worst_mm':>12}{'hunts':>12}")
    print('-' * 96)
    for key in keys:
        a = np.array(agg[key])
        print(f"{str(key):<24}"
              f"{a[:,0].mean():>8.1f}+/-{a[:,0].std():>4.1f}"
              f"{a[:,1].mean():>10.1f}+/-{a[:,1].std():>4.1f}"
              f"{a[:,2].mean():>9.3f}+/-{a[:,2].std():.3f}"
              f"{a[:,3].mean():>7.2f}+/-{a[:,3].std():.2f}"
              f"{a[:,4].mean():>7.1f}+/-{a[:,4].std():.1f}")


def main():
    os.makedirs('out', exist_ok=True)
    print(f"Focus bands: visible |err|<{BAND_VISIBLE} mm, "
          f"pixel-level (1px CoC at {NATIVE_PIX_UM} um) |err|<{BAND_PIXEL:.3f} mm")

    # -------- Experiment 1: scenarios x configs (at 60 fps) --------
    jobs = []
    for scenario in SCENARIOS:
        for (label, kind, lat) in CONFIGS:
            for seed in range(N_SEEDS):
                jobs.append(((scenario, label), seed, kind, lat, scenario,
                             P_DROPOUT, DROPOUT_CONF, False, FPS_BASE))

    # -------- Experiment 2: dropout grid (H1, 60 fps) ---------------
    for p in GRID_P:
        for cf in GRID_CONF:
            for (label, kind, lat) in CONFIGS[:2]:        # AF-S, AF-C
                for seed in range(N_SEEDS_GRID):
                    jobs.append((('grid', label, p, cf), seed, kind, lat,
                                 'walk', p, cf, False, FPS_BASE))

    # -------- Experiment 3: burst blackout (H4, 60 fps) -------------
    for (label, kind, lat) in CONFIGS:
        for seed in range(N_SEEDS):
            jobs.append((('burst', label), seed, kind, lat, 'walk',
                         P_DROPOUT, DROPOUT_CONF, True, FPS_BASE))

    # -------- Experiment 4: AF-loop-rate sweep (H5) ------------------
    # AF-S vs AF-C (with realistic 33 ms latency) at 15/30/60 fps,
    # both scenarios, default dropout. The question: does the AF-C
    # conclusion survive if the X2D's AF loop is only a detector-style
    # subrate of the 60 fps EVF stream?
    for fps in FPS_SWEEP:
        for scenario in SCENARIOS:
            for (label, kind, lat) in [('AF-S', 'afs', 0.0),
                                       ('AF-C +33ms lat', 'afc', ISP_LATENCY_MS)]:
                for seed in range(N_SEEDS_FPS):
                    jobs.append((('fps', fps, scenario, label), seed, kind,
                                 lat, scenario, P_DROPOUT, DROPOUT_CONF,
                                 False, fps))

    print(f"Running {len(jobs)} trials...")
    t0 = time.time()
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(_worker, jobs, chunksize=50))
    print(f"Done in {time.time() - t0:.1f} s.")

    agg = {}
    for key, *vals in results:
        agg.setdefault(key, []).append(vals)

    # ---- tables ----
    for scenario in SCENARIOS:
        print_table(
            f"{scenario} subject ({N_SEEDS} seeds, 60 fps, {int(P_DROPOUT*100)}% "
            f"dropout @ conf {DROPOUT_CONF})",
            {l: agg[(scenario, l)] for l, *_ in CONFIGS},
            [l for l, *_ in CONFIGS])

    print_table(
        f"burst blackout ({N_SEEDS} seeds, 60 fps, walk + dropout + "
        f"{BLACKOUT_S*1000:.0f}ms blind per {BURST_PERIOD_S*1000:.0f}ms shot)",
        {l: agg[('burst', l)] for l, *_ in CONFIGS},
        [l for l, *_ in CONFIGS])

    # ---- H5: fps sweep tables ----
    for scenario in SCENARIOS:
        print(f"\n=== H5 AF-loop-rate sweep -- {scenario} "
              f"({N_SEEDS_FPS} seeds, {int(P_DROPOUT*100)}% dropout) ===")
        print(f"{'loop rate':<12}{'config':<18}{'track% (0.3mm)':>16}"
              f"{'mean_lag_mm':>14}{'worst_mm':>12}{'hunts':>10}")
        print('-' * 82)
        for fps in FPS_SWEEP:
            for label in ['AF-S', 'AF-C +33ms lat']:
                a = np.array(agg[('fps', fps, scenario, label)])
                print(f"{fps:>3d} fps     {label:<18}"
                      f"{a[:,0].mean():>8.1f}+/-{a[:,0].std():>4.1f}"
                      f"{a[:,2].mean():>9.3f}+/-{a[:,2].std():.3f}"
                      f"{a[:,3].mean():>7.2f}+/-{a[:,3].std():.2f}"
                      f"{a[:,4].mean():>6.1f}+/-{a[:,4].std():.1f}")

    # ---- H5 figure ----
    plt.rcParams.update({'font.size': 12})
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), sharey=True)
    width = 0.35
    x = np.arange(len(FPS_SWEEP))
    for ax, scenario in zip(axes, SCENARIOS):
        for off, (label, color) in zip([-width / 2, width / 2],
                                       [('AF-S', 'C3'),
                                        ('AF-C +33ms lat', 'C2')]):
            means = [np.array(agg[('fps', fps, scenario, label)])[:, 0].mean()
                     for fps in FPS_SWEEP]
            stds = [np.array(agg[('fps', fps, scenario, label)])[:, 0].std()
                    for fps in FPS_SWEEP]
            bars = ax.bar(x + off, means, width, yerr=stds, capsize=5,
                          color=color, alpha=0.85, edgecolor='black',
                          linewidth=1.1, label=label)
            for b, m, s in zip(bars, means, stds):
                ax.text(b.get_x() + b.get_width() / 2, min(m + s + 2, 112),
                        f'{m:.0f}', ha='center', va='bottom',
                        fontsize=10, fontweight='bold')
        ax.set_xticks(x, [f'{fps} fps' for fps in FPS_SWEEP])
        ax.set_xlabel('AF measurement loop rate')
        ax.set_title(f'{scenario} subject', fontweight='bold')
        ax.set_ylim(0, 118)
        ax.grid(True, axis='y', alpha=0.3)
    axes[0].set_ylabel('track% (|err| < 0.3 mm)')
    axes[0].legend(loc='lower right', fontsize=10)
    fig.suptitle('H5: does the AF-C conclusion survive a slower AF loop?\n'
                 '60 fps = EVF stream rate (verified); 30 / 15 fps = '
                 'detector-style subrates (FW 3.1 face detection lower bound)',
                 fontsize=12, fontweight='bold')
    fig.tight_layout()
    out4 = os.path.join('out', 'afc_fps_sweep.png')
    fig.savefig(out4, dpi=200, bbox_inches='tight')
    print(f"\nSaved -> {out4}")

    # ---- dropout grid table + heatmap (H1) ----
    print(f"\n=== dropout grid: track% (0.3mm band), walk, 60 fps, "
          f"{N_SEEDS_GRID} seeds ===")
    print("rows = P(dropout), cols = dropout confidence floor")
    hdr = "            " + "".join(f"{cf:>10.2f}" for cf in GRID_CONF)
    grids = {}
    for label, *_ in CONFIGS[:2]:
        g = np.zeros((len(GRID_P), len(GRID_CONF)))
        for i, p in enumerate(GRID_P):
            for j, cf in enumerate(GRID_CONF):
                g[i, j] = np.array(agg[('grid', label, p, cf)])[:, 0].mean()
        grids[label] = g
        print(f"\n{label}:")
        print(hdr)
        for i, p in enumerate(GRID_P):
            print(f"  P={p:<8.1f}" + "".join(f"{g[i,j]:>10.1f}"
                                             for j in range(len(GRID_CONF))))

    adv = grids['AF-C'] - grids['AF-S']
    print("\nAF-C advantage (percentage points):")
    print(hdr)
    for i, p in enumerate(GRID_P):
        print(f"  P={p:<8.1f}" + "".join(f"{adv[i,j]:>10.1f}"
                                         for j in range(len(GRID_CONF))))

    plt.rcParams.update({'font.size': 11})
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    for ax, (name, g) in zip(axes, [('AF-S track%', grids['AF-S']),
                                    ('AF-C track%', grids['AF-C']),
                                    ('AF-C advantage (pp)', adv)]):
        vmax = 100 if 'advantage' not in name else max(abs(adv).max(), 1)
        vmin = 0 if 'advantage' not in name else -vmax
        cmap = 'viridis' if 'advantage' not in name else 'RdBu'
        im = ax.imshow(g, cmap=cmap, vmin=vmin, vmax=vmax, aspect='auto')
        ax.set_xticks(range(len(GRID_CONF)), [f'{c:.2f}' for c in GRID_CONF])
        ax.set_yticks(range(len(GRID_P)), [f'{p:.1f}' for p in GRID_P])
        ax.set_xlabel('dropout confidence floor')
        ax.set_ylabel('P(dropout)')
        ax.set_title(name, fontweight='bold')
        for i in range(len(GRID_P)):
            for j in range(len(GRID_CONF)):
                ax.text(j, i, f'{g[i,j]:.0f}', ha='center', va='center',
                        fontsize=9,
                        color='white' if cmap == 'viridis' and g[i, j] < 60
                        else 'black')
        fig.colorbar(im, ax=ax, shrink=0.85)
    fig.suptitle('Dropout grid: where the AF-C advantage lives -- and where it vanishes\n'
                 '(right edge: dropout above AF-S trust threshold, invisible to both; '
                 'left edge: below AF-C sweep floor, both degrade)',
                 fontsize=12, fontweight='bold')
    fig.tight_layout()
    out2 = os.path.join('out', 'afc_dropout_grid.png')
    fig.savefig(out2, dpi=200, bbox_inches='tight')
    print(f"Saved -> {out2}")

    # ---- trajectory plots: walk + erratic (60 fps) ----
    seed0 = 7
    styles = {'AF-S': ('C3', '--'),
              'AF-C': ('C0', '-'),
              'AF-C +33ms lat': ('C2', '-')}
    n_frames = int(WINDOW_S * FPS_BASE)
    t = np.arange(n_frames) / FPS_BASE

    fig, axes = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    for ax, scenario in zip(axes, SCENARIOS):
        traces = {label: run_trial(seed0, kind, lat, scenario,
                                   P_DROPOUT, DROPOUT_CONF, False, FPS_BASE,
                                   want_trace=True)
                  for (label, kind, lat) in CONFIGS}
        truth = traces[CONFIGS[0][0]]['truths']
        ax.plot(t, truth, 'k-', lw=3, label='subject (true focus)', alpha=0.85)
        ax.fill_between(t, truth - BAND_VISIBLE, truth + BAND_VISIBLE,
                        color='k', alpha=0.08, label=f'±{BAND_VISIBLE} mm band')
        for label, *_ in CONFIGS:
            col, ls = styles[label]
            ax.plot(t, traces[label]['cmds'], color=col, ls=ls, lw=2,
                    label=label, alpha=0.9)
        ax.axvspan(0, ACQUIRE_GRACE_S, color='gray', alpha=0.08)
        ax.set_ylabel('focus position (mm)')
        ax.set_title(f'{scenario} subject', fontweight='bold')
        ax.legend(loc='best', fontsize=9)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel('time (s)')
    fig.suptitle('AF-C depth tracking on IMX461 sim (60 fps, 30% confidence dropout)\n'
                 'same Kalman stack as the AF-S study, velocity state on',
                 fontsize=13, fontweight='bold')
    fig.tight_layout()
    out1 = os.path.join('out', 'afc_tracking.png')
    fig.savefig(out1, dpi=200, bbox_inches='tight')
    print(f"Saved -> {out1}")

    # ---- burst trajectory ----
    fig, ax = plt.subplots(figsize=(12, 6))
    traces = {label: run_trial(seed0, kind, lat, 'walk',
                               P_DROPOUT, DROPOUT_CONF, True, FPS_BASE,
                               want_trace=True)
              for (label, kind, lat) in CONFIGS}
    truth = traces[CONFIGS[0][0]]['truths']
    ax.plot(t, truth, 'k-', lw=3, label='subject (true focus)', alpha=0.85)
    ax.fill_between(t, truth - BAND_VISIBLE, truth + BAND_VISIBLE,
                    color='k', alpha=0.08, label=f'±{BAND_VISIBLE} mm band')
    for label, *_ in CONFIGS:
        col, ls = styles[label]
        ax.plot(t, traces[label]['cmds'], color=col, ls=ls, lw=2,
                label=label, alpha=0.9)
    blacked = False
    for k in range(n_frames):
        tk = k / FPS_BASE
        if tk >= BURST_START_S and ((tk - BURST_START_S) % BURST_PERIOD_S) < BLACKOUT_S:
            ax.axvspan(k / FPS_BASE, (k + 1) / FPS_BASE, color='C1', alpha=0.10,
                       label='PDAF blackout (exposure/readout)'
                       if not blacked else None)
            blacked = True
    ax.set_xlabel('time (s)')
    ax.set_ylabel('focus position (mm)')
    ax.set_title('Burst AF-C: PDAF blind windows at ~3.3 fps cadence\n'
                 'stateless holds through the gap; Kalman coasts on velocity',
                 fontsize=12, fontweight='bold')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out3 = os.path.join('out', 'afc_burst.png')
    fig.savefig(out3, dpi=200, bbox_inches='tight')
    print(f"Saved -> {out3}")


if __name__ == '__main__':
    main()
