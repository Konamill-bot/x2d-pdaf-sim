"""Thin-lens defocus PSF and dual-pixel sub-aperture split.

A defocused point source forms a circle of confusion (CoC) on the sensor.
The PDAF pixels on a dual-pixel sensor see the scene through the LEFT and
RIGHT halves of the lens aperture, so each "sees" a half-disk PSF.

When the scene is in focus, left and right images are identical (zero disparity).
When defocused, they shift laterally in opposite directions — magnitude and
SIGN of the shift encode how far and which way the lens must move.
"""
from __future__ import annotations
import numpy as np


def coc_radius_px(defocus_mm: float, f_mm: float, fnum: float,
                  subject_dist_mm: float, pixel_pitch_um: float) -> float:
    """Circle-of-confusion radius in pixels for a given focus error.

    defocus_mm: signed lens-position error from in-focus (mm at the lens).
    Positive = focused beyond subject (back-focus).
    """
    # Magnification-based CoC approximation (thin lens, small defocus).
    # CoC_diameter ~= |defocus| * f / (N * (subject_dist - f))
    if subject_dist_mm <= f_mm:
        subject_dist_mm = f_mm + 1.0
    coc_mm = abs(defocus_mm) * f_mm / (fnum * (subject_dist_mm - f_mm))
    coc_um = coc_mm * 1000.0
    return 0.5 * coc_um / pixel_pitch_um  # radius in px


def disk_kernel(radius_px: float, half: str | None = None) -> np.ndarray:
    """Disk PSF kernel. half=None -> full disk; 'L' or 'R' -> half disk."""
    r = max(radius_px, 0.5)
    size = int(np.ceil(2 * r)) | 1  # odd
    c = size // 2
    y, x = np.mgrid[:size, :size] - c
    mask = (x * x + y * y) <= (r * r)
    if half == 'L':
        mask &= (x <= 0)
    elif half == 'R':
        mask &= (x >= 0)
    k = mask.astype(np.float32)
    s = k.sum()
    return k / s if s > 0 else k


def signed_disparity_px(defocus_mm: float, f_mm: float, fnum: float,
                        subject_dist_mm: float, pixel_pitch_um: float) -> float:
    """Expected disparity (px) between L and R sub-aperture images.

    The centroid of a half-disk of radius r lies at 4r/(3*pi) from the diameter.
    So |L_centroid - R_centroid| = 8r/(3*pi). Sign tracks defocus direction.
    """
    r = coc_radius_px(defocus_mm, f_mm, fnum, subject_dist_mm, pixel_pitch_um)
    mag = 8.0 * r / (3.0 * np.pi)
    return float(np.sign(defocus_mm) * mag)
