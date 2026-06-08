"""Synthesize left/right dual-pixel views from a sharp image + defocus state."""
from __future__ import annotations
import numpy as np
from scipy.signal import fftconvolve
from .psf import disk_kernel, coc_radius_px


def _bin_2d(img: np.ndarray, factor: int) -> np.ndarray:
    """Mean-pool image by integer factor (binning). Crops to multiple."""
    if factor <= 1:
        return img
    h, w = img.shape
    h2 = (h // factor) * factor
    w2 = (w // factor) * factor
    img = img[:h2, :w2]
    return img.reshape(h2 // factor, factor, w2 // factor, factor).mean(axis=(1, 3))


def render_lr(sharp: np.ndarray, defocus_mm: float, f_mm: float, fnum: float,
              subject_dist_mm: float, pixel_pitch_um: float,
              noise_sigma: float = 0.005,
              bin_factor: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Return (left_view, right_view) for the given lens-position error.

    bin_factor > 1 simulates sensor binning during AF readout. Real cameras
    use this to reduce readout time and ISP load during AF scan -- 4x4 bin
    gives ~16x lower data rate at the cost of disparity precision (still
    adequate for AF). Sony / Canon / Nikon all do this. Unknown whether
    X2D does it aggressively.
    """
    r = coc_radius_px(defocus_mm, f_mm, fnum, subject_dist_mm, pixel_pitch_um)
    if r < 0.6:
        L = sharp.copy()
        R = sharp.copy()
    else:
        kL = disk_kernel(r, half='L')
        kR = disk_kernel(r, half='R')
        L = fftconvolve(sharp, kL, mode='same')
        R = fftconvolve(sharp, kR, mode='same')
    if noise_sigma > 0:
        # Binning averages noise -> sigma scales by 1/factor.
        eff_sigma = noise_sigma / max(1, bin_factor)
        L = L + np.random.normal(0, eff_sigma, L.shape).astype(np.float32)
        R = R + np.random.normal(0, eff_sigma, R.shape).astype(np.float32)
    if bin_factor > 1:
        L = _bin_2d(L, bin_factor)
        R = _bin_2d(R, bin_factor)
    return L.astype(np.float32), R.astype(np.float32)
