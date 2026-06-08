"""Compare StatelessPolicy vs TemporalPolicy on a synthetic AF sequence.

Scenario: subject at fixed distance with small periodic motion. Lens starts
mis-focused. Each frame we render dual-pixel views, run phase correlation,
hand (disparity, confidence) to a policy that commands a new lens position.

Everything runs in millimetres of lens defocus -- disparity (px) is converted
to mm at the seam, so the policy never sees mixed units. This keeps the
RMS numbers honest.
"""
from __future__ import annotations
import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity
from pdaf_sim.policy import StatelessPolicy, TemporalPolicy
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.scene import SCENES

# X2D-like optics (XCD 55V on 100MP BSI sensor).
F_MM = 55.0
FNUM = 2.5
SUBJ_DIST_MM = 1500.0
PIX_UM = 3.76
N_FRAMES = 120

# Linear coefficient: 1 mm of lens defocus -> this many px of disparity.
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM)


def simulate(scene_name: str, policy, seed: int):
    rng = np.random.default_rng(seed)
    sharp = SCENES[scene_name](rng)

    # True subject focus position (mm); small breathing motion.
    t = np.arange(N_FRAMES)
    true_focus_mm = 2.5 + 0.3 * np.sin(2 * np.pi * t / 40.0)

    lens_mm = 0.0
    sweeps = 0
    cmds = []

    for k in range(N_FRAMES):
        err_mm = lens_mm - true_focus_mm[k]
        L, R = render_lr(sharp, err_mm, F_MM, FNUM, SUBJ_DIST_MM, PIX_UM,
                         noise_sigma=0.01)
        disp_px, conf = estimate_disparity(L, R)
        disp_mm = disp_px / PX_PER_MM if abs(PX_PER_MM) > 1e-9 else 0.0

        # Policy works in mm. lens position == lens_mm. Disparity is the
        # signed lens error PDAF thinks it sees, so "drive toward zero
        # disparity" means lens_mm -= disp_mm.
        decision = policy.step(disp_mm, conf, lens_mm)
        if decision.swept:
            sweeps += 1
        lens_mm = decision.lens_cmd
        cmds.append(lens_mm)

    cmds = np.array(cmds)
    deltas = np.diff(cmds)
    hunt_osc = int(np.sum((np.abs(deltas[:-1]) > 0.05) &
                          (np.sign(deltas[:-1]) != np.sign(deltas[1:]))))
    rms_err = float(np.sqrt(np.mean((cmds - true_focus_mm) ** 2)))
    in_focus_frac = float(np.mean(np.abs(cmds - true_focus_mm) < 0.3))
    return {
        'sweeps': sweeps,
        'hunt_osc': hunt_osc,
        'rms_err_mm': rms_err,
        'in_focus_frac': in_focus_frac,
        'cmds': cmds,
        'truth': true_focus_mm,
    }


def main():
    os.makedirs('out', exist_ok=True)

    # Same seed for both policies per scene -> identical measurement stream.
    results = {}
    for scene in SCENES:
        results[scene] = {
            'stateless': simulate(scene, StatelessPolicy(tau=0.35,  sweep_step=0.2), seed=1),
            'temporal':  simulate(scene, TemporalPolicy(tau_sweep=0.08, sweep_step=0.2), seed=1),
        }

    print(f"\n{'scene':<16}{'policy':<12}{'sweeps':>8}{'hunt_osc':>10}"
          f"{'rms_err_mm':>14}{'in_focus%':>12}")
    print('-' * 72)
    for scene, by_policy in results.items():
        for name, r in by_policy.items():
            print(f"{scene:<16}{name:<12}{r['sweeps']:>8d}{r['hunt_osc']:>10d}"
                  f"{r['rms_err_mm']:>14.3f}{100*r['in_focus_frac']:>11.1f}%")

    fig, axes = plt.subplots(len(SCENES), 1, figsize=(10, 8), sharex=True)
    for ax, (scene, by_policy) in zip(axes, results.items()):
        ax.plot(by_policy['stateless']['truth'], 'k--', label='true focus', alpha=0.6)
        ax.plot(by_policy['stateless']['cmds'], label='stateless', alpha=0.85)
        ax.plot(by_policy['temporal']['cmds'],  label='temporal (Kalman)', alpha=0.85)
        s = by_policy['stateless']; t = by_policy['temporal']
        ax.set_title(
            f"{scene}  |  sweeps {s['sweeps']}->{t['sweeps']}   "
            f"rms {s['rms_err_mm']:.2f}->{t['rms_err_mm']:.2f} mm   "
            f"in-focus {100*s['in_focus_frac']:.0f}%->{100*t['in_focus_frac']:.0f}%"
        )
        ax.set_ylabel('lens pos (mm)')
        ax.legend(loc='best', fontsize=8)
    axes[-1].set_xlabel('frame')
    fig.suptitle('PDAF decision-policy comparison  (X2D-like optics)')
    fig.tight_layout()
    out = os.path.join('out', 'policy_comparison.png')
    fig.savefig(out, dpi=130)
    print(f"\nSaved plot -> {out}")


if __name__ == '__main__':
    main()
