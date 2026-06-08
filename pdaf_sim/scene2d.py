"""2D scene with a moving subject.

A scene is a (H, W) image. Within it lives a subject -- a small
high-contrast patch at (x, y) with a focus distance. The subject moves
over time. At each frame we render the FULL frame at the subject's
current defocus, with the subject inserted at its current (x, y).

This is the minimal extension needed to study:
  - spatial PDAF gradient (zone-grid disparity field)
  - subject persistence across brief occlusions
  - object-detection-style 2D tracking

Kept deliberately simple: one moving subject, optional occlusion,
synthetic background.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

H, W = 192, 256          # frame size (much smaller than 100MP for speed)
SUBJ_SIZE = 24           # subject patch size in pixels


@dataclass
class Subject:
    """A small textured patch that moves and has its own focus distance."""
    x: float                # column (0..W)
    y: float                # row (0..H)
    focus_mm: float = 2.5   # subject distance (in lens-defocus mm units)


def make_background(rng: np.random.Generator, kind: str = 'low_contrast') -> np.ndarray:
    """Background fills the frame; subject is painted on top."""
    if kind == 'low_contrast':
        grad_x = np.linspace(0.45, 0.55, W, dtype=np.float32)
        bg = np.tile(grad_x, (H, 1))
        bg += rng.normal(0, 0.01, bg.shape)
    elif kind == 'high_contrast':
        bg = np.zeros((H, W), dtype=np.float32)
        for _ in range(60):
            x = rng.integers(0, W)
            wpx = rng.integers(2, 6)
            bg[:, max(0, x-wpx):x+wpx] += rng.uniform(0.2, 0.7)
        bg = bg / bg.max() * 0.5
    else:
        bg = 0.5 * np.ones((H, W), dtype=np.float32)
    return np.clip(bg, 0, 1).astype(np.float32)


def make_subject_patch(rng: np.random.Generator, size: int = SUBJ_SIZE) -> np.ndarray:
    """High-contrast textured patch (the 'subject') -- alternating stripes."""
    p = np.zeros((size, size), dtype=np.float32)
    for i in range(0, size, 3):
        p[:, i:i+2] = 1.0
    # Add a darker inner box for visual interest.
    p[size//4:3*size//4, size//4:3*size//4] *= 0.3
    p += rng.normal(0, 0.02, p.shape)
    return np.clip(p, 0, 1).astype(np.float32)


def render_frame_with_subject(bg: np.ndarray, subj_patch: np.ndarray,
                              subj: Subject, occluded: bool = False) -> np.ndarray:
    """Place the subject patch onto a copy of bg at (subj.x, subj.y)."""
    out = bg.copy()
    if occluded:
        return out
    s = subj_patch.shape[0]
    x0 = int(subj.x) - s // 2
    y0 = int(subj.y) - s // 2
    # Clip to frame
    sx0, sy0 = max(0, -x0), max(0, -y0)
    x0c, y0c = max(0, x0), max(0, y0)
    x1c, y1c = min(W, x0 + s), min(H, y0 + s)
    if x1c > x0c and y1c > y0c:
        out[y0c:y1c, x0c:x1c] = subj_patch[sy0:sy0 + (y1c - y0c),
                                            sx0:sx0 + (x1c - x0c)]
    return out


def zone_grid(n_cols: int = 5, n_rows: int = 3) -> list[tuple[int, int, int, int]]:
    """Return list of (row_lo, row_hi, col_lo, col_hi) rectangles tiling the frame."""
    zones = []
    h_step = H // n_rows
    w_step = W // n_cols
    for r in range(n_rows):
        for c in range(n_cols):
            r0, r1 = r * h_step, (r + 1) * h_step if r < n_rows - 1 else H
            c0, c1 = c * w_step, (c + 1) * w_step if c < n_cols - 1 else W
            zones.append((r0, r1, c0, c1))
    return zones


def zone_centers(n_cols: int = 5, n_rows: int = 3) -> list[tuple[float, float]]:
    """(x_center, y_center) for each zone, matching zone_grid order."""
    centers = []
    h_step = H / n_rows
    w_step = W / n_cols
    for r in range(n_rows):
        for c in range(n_cols):
            centers.append((c * w_step + w_step / 2, r * h_step + h_step / 2))
    return centers
