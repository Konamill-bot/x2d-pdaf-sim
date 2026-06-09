"""Sony IMX461 sensor geometry as used in X2D 100C.

Native sensor: 43.8 x 32.9 mm, 11656 x 8750 px, 3.76 um pitch.
At 4x4 binning (the mode the EVF uses): ~2914 x 2187 px, ~6.4 MP.

X2D spec: 294 PDAF zones covering ~97% of sensor.
With a 21 (cols) x 14 (rows) uniform grid -> 294 zones exactly.

We simulate the binned readout dimensions but at half scale for
compute tractability: 1457 x 1094 px frame, zones approximately
69 x 78 px each. Per-zone phase correlation operates on real zone
sub-images, so the disparity-vs-defocus physics remains correct in
units of px / mm / aperture.

Multi-zone selection: real cameras do not run phase correlation on
all 294 zones every frame -- they pick the N nearest to the subject
(detected by some mechanism) and aggregate those. N=5 to 9 is typical.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

# Native IMX461
NATIVE_W_PX = 11656
NATIVE_H_PX = 8750
NATIVE_PIX_UM = 3.76
SENSOR_W_MM = 43.8
SENSOR_H_MM = 32.9

# 4x4 binned readout
BIN = 4
BIN_W_PX = NATIVE_W_PX // BIN     # 2914
BIN_H_PX = NATIVE_H_PX // BIN     # 2187
BIN_PIX_UM = NATIVE_PIX_UM * BIN  # 15.04

# Sim scale: half of binned (kept tractable for 1000-seed runs)
SIM_SCALE = 0.5
SIM_W_PX = int(BIN_W_PX * SIM_SCALE)   # 1457
SIM_H_PX = int(BIN_H_PX * SIM_SCALE)   # 1093
SIM_PIX_UM = BIN_PIX_UM / SIM_SCALE    # 30.08 um per sim px

# Zone grid: 21 x 14 = 294
N_COLS = 21
N_ROWS = 14
N_ZONES = N_COLS * N_ROWS

# Active AF area covers 97% of sensor centred
ACTIVE_FRAC = 0.97


@dataclass(frozen=True)
class Zone:
    idx: int
    row: int
    col: int
    r0: int          # row pixel range (inclusive)
    r1: int
    c0: int
    c1: int
    cx: float        # zone centre in px
    cy: float


def make_zones() -> list[Zone]:
    """Tile the active AF area into 21x14 zones."""
    pad_w = int(SIM_W_PX * (1 - ACTIVE_FRAC) / 2)
    pad_h = int(SIM_H_PX * (1 - ACTIVE_FRAC) / 2)
    active_w = SIM_W_PX - 2 * pad_w
    active_h = SIM_H_PX - 2 * pad_h
    w_step = active_w / N_COLS
    h_step = active_h / N_ROWS
    zones: list[Zone] = []
    for r in range(N_ROWS):
        for c in range(N_COLS):
            c0 = int(pad_w + c * w_step)
            c1 = int(pad_w + (c + 1) * w_step)
            r0 = int(pad_h + r * h_step)
            r1 = int(pad_h + (r + 1) * h_step)
            zones.append(Zone(
                idx=r * N_COLS + c, row=r, col=c,
                r0=r0, r1=r1, c0=c0, c1=c1,
                cx=(c0 + c1) / 2, cy=(r0 + r1) / 2,
            ))
    return zones


ZONES = make_zones()


def nearest_zones(subj_x: float, subj_y: float, n: int) -> list[Zone]:
    """N PDAF zones closest to (subj_x, subj_y) in pixel space."""
    return sorted(
        ZONES,
        key=lambda z: (z.cx - subj_x) ** 2 + (z.cy - subj_y) ** 2,
    )[:n]


def zone_contains(z: Zone, x: float, y: float) -> bool:
    return z.c0 <= x < z.c1 and z.r0 <= y < z.r1
