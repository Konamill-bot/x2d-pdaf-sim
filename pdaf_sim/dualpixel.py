"""Synthesize left/right dual-pixel views from a sharp image + defocus state."""
from __future__ import annotations
import numpy as np
from scipy.signal import fftconvolve
from .psf import disk_kernel, coc_radius_px


def _subpixel_shift_x(img: np.ndarray, dx: float) -> np.ndarray:
    """Shift image along axis=1 by a (possibly sub-pixel) amount via FFT."""
    n = img.shape[1]
    f = np.fft.rfft(img, axis=1)
    k = np.fft.rfftfreq(n)
    f *= np.exp(-2j * np.pi * k * dx)[None, :]
    return np.fft.irfft(f, n, axis=1).astype(np.float32)


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
              bin_factor: int = 1,
              rng: np.random.Generator | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return (left_view, right_view) for the given lens-position error.

    bin_factor > 1 simulates sensor binning during AF readout. Real cameras
    use this to reduce readout time and ISP load during AF scan -- 4x4 bin
    gives ~16x lower data rate at the cost of disparity precision (still
    adequate for AF). Sony / Canon / Nikon all do this. Unknown whether
    X2D does it aggressively.
    """
    r = coc_radius_px(defocus_mm, f_mm, fnum, subject_dist_mm, pixel_pitch_um)
    if r < 0.05:
        L = sharp.copy()
        R = sharp.copy()
    else:
        # Sub-aperture model: blur by the full defocus disk, then shift
        # L and R by the half-disk centroid offset (+/- 4r/3pi) via
        # Fourier sub-pixel shift. The displacement -- which is the PDAF
        # signal -- is exact at any magnitude, including deep sub-pixel.
        # (A discrete half-disk kernel quantizes to a delta below ~1 px
        # radius, creating an artificial dead zone ~0.3 mm wide; real
        # masked-pixel PDAF resolves sub-pixel disparity, so the shift
        # must be modelled continuously.)
        # Sign: front- vs back-focus mirrors the shift direction.
        if r >= 0.6:
            blurred = fftconvolve(sharp, disk_kernel(r, half=None), mode='same')
        else:
            blurred = sharp
        c = 4.0 * r / (3.0 * np.pi) * (-1.0 if defocus_mm < 0 else 1.0)
        L = _subpixel_shift_x(blurred, +c)
        R = _subpixel_shift_x(blurred, -c)
    if noise_sigma > 0:
        # Binning averages noise -> sigma scales by 1/factor.
        eff_sigma = noise_sigma / max(1, bin_factor)
        _rng = rng if rng is not None else np.random.default_rng()
        L = L + _rng.normal(0, eff_sigma, L.shape).astype(np.float32)
        R = R + _rng.normal(0, eff_sigma, R.shape).astype(np.float32)
    if bin_factor > 1:
        L = _bin_2d(L, bin_factor)
        R = _bin_2d(R, bin_factor)
    return L.astype(np.float32), R.astype(np.float32)
