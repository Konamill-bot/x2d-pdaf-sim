"""Standardized AF test scenarios.

Each benchmark returns a *driver* function (subject_focus_mm_over_time,
scene_name) that the runner uses to generate a measurement stream and a
ground-truth lens trajectory.

The same protocols are designed to be reproducible on a real camera:
the README documents the physical setup for each, so simulated results
and real-camera results can be put on the same axes.
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Callable


@dataclass
class Benchmark:
    name: str
    scene: str                       # which scene from scene.py
    n_frames: int
    truth_mm: np.ndarray             # subject focus position over time, mm
    shutter_frames: list             # frame indices at which a shot fires
    description: str                 # how to reproduce on a real camera
    requires_afc: bool = False       # True -> only AF-C bodies can run this
                                     #         (X2D is AF-S only -> skip)


def bench_static_lowcontrast() -> Benchmark:
    """T1: low-contrast static subject. Tests pure hunting behaviour."""
    n = 120
    return Benchmark(
        name='T1_static_lowcontrast',
        scene='low_contrast',
        n_frames=n,
        truth_mm=np.full(n, 2.5),
        shutter_frames=[],
        description=(
            "Aim at a smooth grey wall under 100 lux. Half-press AF for "
            "5 seconds. Record lens motor commands (or screen-recorded "
            "magnified-view) over time."
        ),
    )


def bench_post_shutter_reset() -> Benchmark:
    """T2: shoot then re-acquire. Tests state-reset behaviour."""
    n = 200
    truth = np.full(n, 2.5)
    return Benchmark(
        name='T2_post_shutter_reset',
        scene='high_contrast',
        n_frames=n,
        truth_mm=truth,
        shutter_frames=[60, 120, 180],
        description=(
            "Static high-contrast subject. AF lock, fire 3 shots ~2s apart. "
            "Measure time-to-reacquire after each shot."
        ),
    )


def bench_step_response() -> Benchmark:
    """T3: subject distance steps suddenly. Tests AF-C / temporal bandwidth."""
    n = 160
    truth = np.where(np.arange(n) < 40, 5.0,
            np.where(np.arange(n) < 80, 0.5,
            np.where(np.arange(n) < 120, 5.0, 0.5))).astype(float)
    return Benchmark(
        name='T3_step_response',
        scene='high_contrast',
        n_frames=n,
        truth_mm=truth,
        shutter_frames=[],
        requires_afc=True,
        description=(
            "Two contrasting targets at different distances behind a "
            "sliding occluder. Switch occluder every ~1s. Half-press "
            "and hold; record settle time and overshoot."
        ),
    )


def bench_repeating_pattern() -> Benchmark:
    """T4: periodic stripes -> ambiguous disparity (should be honest about failure)."""
    n = 120
    return Benchmark(
        name='T4_repeating_pattern',
        scene='repeating',
        n_frames=n,
        truth_mm=np.full(n, 2.5),
        shutter_frames=[],
        description=(
            "Fine periodic stripe target (e.g. 1.5mm pitch at 1.5m). "
            "Half-press; observe whether the policy hunts indefinitely "
            "or gives up gracefully."
        ),
    )


def bench_breathing() -> Benchmark:
    """T5: small periodic motion. Original demo scenario."""
    n = 120
    t = np.arange(n)
    return Benchmark(
        name='T5_breathing',
        scene='low_contrast',
        n_frames=n,
        truth_mm=2.5 + 0.3 * np.sin(2 * np.pi * t / 40.0),
        shutter_frames=[],
        requires_afc=True,
        description=(
            "Subject performs small ~1Hz sway. Tests whether the policy "
            "tracks subtle motion or treats it as noise."
        ),
    )


def bench_near_focus_psr_drop() -> Benchmark:
    """T6 -- targets the L1.4 near-focus failure mode.

    Subject is at lens_pos ~ 0 from start (so error is ~0). A stateless policy
    that relies purely on PSR will see low confidence (because L/R are
    near-identical -> broad correlation peak) and mistakenly fall back to
    CDAF / give up. A composite-confidence policy with near-focus bonus
    should hold the in-focus position.
    """
    n = 80
    return Benchmark(
        name='T6_near_focus_psr_drop',
        scene='high_contrast',
        n_frames=n,
        truth_mm=np.zeros(n),          # in focus from the start
        shutter_frames=[],
        description=(
            "Already-in-focus high-contrast subject. Half-press AF. "
            "A buggy policy fails because PSR drops at the in-focus point. "
            "Lens trajectory should remain near zero with no sweeps."
        ),
    )


ALL_BENCHMARKS = [
    bench_static_lowcontrast,
    bench_post_shutter_reset,
    bench_step_response,
    bench_repeating_pattern,
    bench_breathing,
    bench_near_focus_psr_drop,
]


def benchmarks_for(body: str) -> list:
    """Return only the benchmarks runnable on the given body."""
    is_afc_capable = body.lower() not in {'x2d', 'x2d_100c', 'x1d'}
    return [b for b in ALL_BENCHMARKS
            if is_afc_capable or not b().requires_afc]


# --- Real-world measurement anchors (from user's own A7 IV testing) ---
# These let us check that simulated policies don't dramatically disagree
# with reality. As more measurements are collected, add them here.
REAL_ANCHORS = {
    'sony_a7iv': {
        'T1_static_lowcontrast': {
            'hunt_passes': 1,                # one sweep then give up
            'hunt_duration_s': 0.7,          # observed on all-white wall
            'gives_up_with_indicator': True, # purple AF box on failure
            'source': "user direct measurement, A7 IV + 50mm f/1.8, white wall",
        },
    },
    'hasselblad_x2d': {
        'T1_static_lowcontrast': {
            'hunt_passes': 2,                # two sweeps then give up
            'hunt_duration_s': None,         # not yet measured precisely
            'gives_up_with_indicator': True, # red AF box on failure
            'source': "user direct measurement, X2D + XCD 55V, fw 4.2.0",
        },
        'T2_post_shutter_reset': {
            'reacquire_time_s': 0.0,         # immediate when same subject
            'state_preserved': True,
            'source': "user direct measurement, X2D + XCD 55V, fw 4.2.0",
            'note': "Earlier hypothesis of state-reset-on-shutter was WRONG.",
        },
        'TC_pdaf_vs_cdaf_signature': {
            # Defocused -> half-press behaviour:
            #   slow exploratory move ("slow to somewhere") then fast
            #   directed climb ("fast to one way and goes to exact place").
            # This is the CDAF hill-climb signature, NOT PDAF jump-then-refine.
            # Interpretation: PDAF appears to act only as a direction hint,
            # while ranging is done by CDAF.
            'pattern': 'explore-then-climb (CDAF-style)',
            'pdaf_used_as': 'direction_hint_only',
            'pdaf_used_for_ranging': False,
            'black_subject': 'hunt twice -> red AF box failure indicator',
            'source': "user direct measurement, X2D + XCD 55V, fw 4.2.0",
        },
    },
}
