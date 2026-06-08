"""Synthetic scenes for AF-zone strips.

We deliberately keep this self-contained (no real image dependency) so the
demo runs out-of-the-box. Three regimes:

  high_contrast : dense edges (textured fabric) -- PDAF works well
  low_contrast  : soft gradient + faint noise   -- PDAF struggles, triggers hunting
  repeating     : periodic stripes              -- ambiguous disparity (aliasing)
"""
from __future__ import annotations
import numpy as np

H, W = 64, 256  # AF-zone strip size


def high_contrast(rng: np.random.Generator) -> np.ndarray:
    img = np.zeros((H, W), dtype=np.float32)
    for _ in range(40):
        x = rng.integers(0, W)
        w = rng.integers(2, 8)
        img[:, max(0, x - w):x + w] += rng.uniform(0.3, 1.0)
    img += rng.normal(0, 0.02, img.shape)
    return np.clip(img / img.max(), 0, 1).astype(np.float32)


def low_contrast(rng: np.random.Generator) -> np.ndarray:
    grad = np.linspace(0.45, 0.55, W, dtype=np.float32)
    img = np.tile(grad, (H, 1))
    img += rng.normal(0, 0.01, img.shape)
    return np.clip(img, 0, 1).astype(np.float32)


def repeating(rng: np.random.Generator) -> np.ndarray:
    x = np.arange(W, dtype=np.float32)
    img = 0.5 + 0.4 * np.sin(2 * np.pi * x / 12.0)
    img = np.tile(img, (H, 1))
    img += rng.normal(0, 0.02, img.shape)
    return np.clip(img, 0, 1).astype(np.float32)


SCENES = {
    'high_contrast': high_contrast,
    'low_contrast': low_contrast,
    'repeating': repeating,
}
