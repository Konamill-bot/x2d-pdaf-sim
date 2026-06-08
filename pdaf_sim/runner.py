"""Drive a policy through a Benchmark and produce a lens trajectory.

Separated from run_experiment.py so that both the experimentation script and
the fitting script use the same code path.
"""
from __future__ import annotations
import numpy as np
from .dualpixel import render_lr
from .phase_corr import estimate_disparity
from .psf import signed_disparity_px
from .scene import SCENES
from .benchmarks import Benchmark

# X2D-like defaults; override per call when modelling a different body.
DEFAULT_OPTICS = dict(F_MM=55.0, FNUM=2.5, SUBJ_DIST_MM=1500.0, PIX_UM=3.76)


def run(bench: Benchmark, policy, seed: int = 1,
        optics: dict | None = None, noise_sigma: float = 0.01) -> dict:
    o = {**DEFAULT_OPTICS, **(optics or {})}
    px_per_mm = signed_disparity_px(1.0, o['F_MM'], o['FNUM'],
                                    o['SUBJ_DIST_MM'], o['PIX_UM'])

    rng = np.random.default_rng(seed)
    sharp = SCENES[bench.scene](rng)

    lens_mm = 0.0
    cmds, confs, swept_flags = [], [], []
    shutter_set = set(bench.shutter_frames)

    for k in range(bench.n_frames):
        err_mm = lens_mm - bench.truth_mm[k]
        L, R = render_lr(sharp, err_mm, o['F_MM'], o['FNUM'],
                         o['SUBJ_DIST_MM'], o['PIX_UM'], noise_sigma=noise_sigma)
        disp_px, conf = estimate_disparity(L, R)
        disp_mm = disp_px / px_per_mm if abs(px_per_mm) > 1e-9 else 0.0

        decision = policy.step(disp_mm, conf, lens_mm)
        lens_mm = decision.lens_cmd

        cmds.append(lens_mm)
        confs.append(conf)
        swept_flags.append(decision.swept)

        if k in shutter_set and hasattr(policy, 'on_shutter'):
            policy.on_shutter()

    cmds = np.array(cmds)
    confs = np.array(confs)
    swept_flags = np.array(swept_flags)
    truth = bench.truth_mm

    deltas = np.diff(cmds)
    hunt_osc = int(np.sum((np.abs(deltas[:-1]) > 0.05) &
                          (np.sign(deltas[:-1]) != np.sign(deltas[1:]))))
    rms_err = float(np.sqrt(np.mean((cmds - truth) ** 2)))
    in_focus_frac = float(np.mean(np.abs(cmds - truth) < 0.3))
    sweeps = int(swept_flags.sum())

    return {
        'cmds': cmds, 'truth': truth, 'conf': confs, 'swept': swept_flags,
        'sweeps': sweeps, 'hunt_osc': hunt_osc,
        'rms_err_mm': rms_err, 'in_focus_frac': in_focus_frac,
    }
