"""v4 demo: subject moving across frame, occluded mid-traverse.

Scenario: a small textured subject moves left-to-right at constant
velocity across a low-contrast background. From frame 60 to 80 it is
occluded (a hand passes in front, say). The subject is at a fixed
focus distance.

Comparison:
  - v1 (1D Kalman, single-zone) : single AF zone at frame centre,
                                  loses signal when subject leaves the
                                  centre zone or is occluded; falls back
                                  to CDAF behaviour.
  - v4 (2D, multi-zone + bbox)  : tracks subject across the zone grid,
                                  predicts position during occlusion,
                                  re-acquires immediately when subject
                                  reappears.

Run:  python scripts/run_v4_tracking.py
Out:  out/v4_tracking.png
"""
from __future__ import annotations
import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.scene2d import (
    Subject, make_background, make_subject_patch, render_frame_with_subject,
    zone_grid, zone_centers, H, W
)
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity, cdaf_score
from pdaf_sim.policy import TemporalPolicy
from pdaf_sim.policy2d import V4Policy
from pdaf_sim.psf import signed_disparity_px

F_MM, FNUM, SUBJ_DIST_MM, PIX_UM = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)
FPS = 60
N_FRAMES = 180          # 3 seconds at 60 fps


def measure_zone(L: np.ndarray, R: np.ndarray, rect, center_xy):
    r0, r1, c0, c1 = rect
    Lp = L[r0:r1, c0:c1]
    Rp = R[r0:r1, c0:c1]
    # Cap max disparity search to half the strip width so small zones still work.
    max_d = max(4, min(32, (c1 - c0) // 3))
    disp_px, conf = estimate_disparity(Lp, Rp, max_disp_px=max_d)
    return {
        'disparity_mm': disp_px / PX_PER_MM,
        'pdaf_conf': conf,
        'cdaf_score': cdaf_score((Lp + Rp) * 0.5),
        'center_xy': center_xy,
    }


def run_scenario(policy_kind: str, seed: int = 1):
    rng = np.random.default_rng(seed)
    noise_rng = np.random.default_rng(seed + 100)
    bg = make_background(rng, 'low_contrast')
    patch = make_subject_patch(rng)

    zones = zone_grid()
    centers = zone_centers()
    centre_zone_index = len(zones) // 2

    subj = Subject(x=20.0, y=H / 2, focus_mm=2.5)
    vel_x = (W - 40) / N_FRAMES * 1.5  # crosses the frame in ~2/3 of horizon
    lens_mm = 0.0

    policy_v1 = TemporalPolicy(tau_sweep=0.08, sweep_step=0.2,
                               process_var=0.5*15/FPS, meas_var_base=1.0)
    policy_v4 = V4Policy()
    policy = policy_v1 if policy_kind == 'v1' else policy_v4

    cmds, errs, tracked, lockedflags = [], [], [], []
    for k in range(N_FRAMES):
        # subject moves
        subj.x += vel_x
        occluded = 60 <= k < 80

        # build the full-frame sharp image with subject placed in it
        sharp = render_frame_with_subject(bg, patch, subj, occluded=occluded)

        # render L/R for the WHOLE frame (single defocus, since subject
        # and bg are at the same plane in this minimal scenario)
        err_mm = lens_mm - subj.focus_mm
        # 2D scene is already small (192x256), skip binning for AF measurements.
        L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                         noise_sigma=0.01, bin_factor=1, rng=noise_rng)

        zone_measurements = []
        for rect, center in zip(zones, centers):
            zone_measurements.append(measure_zone(L, R, rect, center))

        if policy_kind == 'v1':
            # v1 only sees the centre zone
            zm = zone_measurements[centre_zone_index]
            decision = policy.step(zm['disparity_mm'], zm['pdaf_conf'], lens_mm)
            tracked.append((float('nan'), float('nan')))
            lockedflags.append(False)
        else:
            decision = policy.step(zone_measurements, lens_mm)
            tracked.append((decision.tracked_x, decision.tracked_y))
            lockedflags.append(decision.locked)

        lens_mm = decision.lens_cmd
        cmds.append(lens_mm)
        errs.append(abs(lens_mm - subj.focus_mm))

    return {
        't': np.arange(N_FRAMES) / FPS,
        'cmds': np.array(cmds),
        'errs': np.array(errs),
        'tracked': np.array(tracked),
        'locked': np.array(lockedflags),
        'occlusion_window': (60 / FPS, 80 / FPS),
    }


def main():
    os.makedirs('out', exist_ok=True)
    r1 = run_scenario('v1')
    r4 = run_scenario('v4')

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)

    ax = axes[0]
    ax.axhline(2.5, color='k', linestyle='--', alpha=0.5, label='subject focus = 2.5 mm')
    ax.axvspan(*r1['occlusion_window'], color='grey', alpha=0.15, label='occlusion')
    ax.plot(r1['t'], r1['cmds'], color='C0', label=f'v1 (1D, centre zone only)',
            linewidth=1.4)
    ax.plot(r4['t'], r4['cmds'], color='C2', label=f'v4 (2D grid + bbox tracker)',
            linewidth=1.4)
    ax.set_ylabel('lens pos (mm)')
    ax.set_title('Lens position vs time — moving subject with mid-traverse occlusion')
    ax.legend(loc='best', fontsize=9)

    ax = axes[1]
    ax.axvspan(*r1['occlusion_window'], color='grey', alpha=0.15)
    ax.plot(r1['t'], r1['errs'], color='C0', label='v1 focus error')
    ax.plot(r4['t'], r4['errs'], color='C2', label='v4 focus error')
    ax.set_ylabel('|lens - subject_focus|  (mm)')
    ax.set_xlabel('time (s)')
    ax.set_title('Absolute focus error vs time')
    ax.legend(loc='best', fontsize=9)

    fig.tight_layout()
    out = os.path.join('out', 'v4_tracking.png')
    fig.savefig(out, dpi=130)

    # Summary -- show stability metrics that reveal v4's real win.
    # Skip initial 0.5s settling period when computing stability.
    settle_idx = int(0.5 * FPS)
    in_focus_threshold = 0.3
    occ_mask = (r1['t'] >= r1['occlusion_window'][0]) & (r1['t'] < r1['occlusion_window'][1])
    after_settle = slice(settle_idx, None)

    def summarize(r, label):
        return {
            'label': label,
            'in_focus_pct': 100 * float(np.mean(r['errs'] < in_focus_threshold)),
            'std_after_settle': float(np.std(r['cmds'][after_settle])),
            'max_err': float(np.max(r['errs'])),
            'catastrophic_events': int(np.sum(r['errs'] > 1.0)),
            'err_during_occl': float(np.mean(r['errs'][occ_mask])),
        }

    s1 = summarize(r1, 'v1')
    s4 = summarize(r4, 'v4')
    print(f"\n{'policy':<6}{'in_focus%':>11}{'std_mm':>10}{'max_err_mm':>12}"
          f"{'>1mm_events':>14}{'err_occl_mm':>13}")
    print('-' * 70)
    for s in (s1, s4):
        print(f"{s['label']:<6}{s['in_focus_pct']:>10.1f}%{s['std_after_settle']:>10.3f}"
              f"{s['max_err']:>12.3f}{s['catastrophic_events']:>14d}{s['err_during_occl']:>13.3f}")
    print(f"\nSaved -> {out}")


if __name__ == '__main__':
    main()
