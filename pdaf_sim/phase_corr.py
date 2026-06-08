"""1-D phase correlation on a horizontal strip + confidence metric.

The confidence score is the normalized peak sharpness of the correlation
surface: peak / second_peak (peak-to-sidelobe ratio). Low PSR == ambiguous
disparity == the cases where the camera SHOULD distrust PDAF and either
sweep with CDAF or fall back on the temporal prior.
"""
from __future__ import annotations
import numpy as np


def _window(n: int) -> np.ndarray:
    return np.hanning(n).astype(np.float32)


def estimate_disparity_multi_zone(L: np.ndarray, R: np.ndarray,
                                   max_disp_px: int = 32,
                                   n_zones: int = 3) -> tuple[float, float]:
    """Split the strip into n_zones horizontal sub-strips, run phase corr on
    each, and return (median disparity, agreement-weighted confidence).

    Agreement-weighted: confidence is amplified when zones agree on disparity,
    damped when they disagree. This fixes a class of failures (false peaks,
    near-focus PSR drop) that single-zone confidence can't detect.
    """
    h = L.shape[0]
    strip_h = max(1, h // n_zones)
    disps, confs = [], []
    for i in range(n_zones):
        lo = i * strip_h
        hi = lo + strip_h if i < n_zones - 1 else h
        d, c = estimate_disparity(L[lo:hi], R[lo:hi], max_disp_px=max_disp_px)
        disps.append(d)
        confs.append(c)
    disps = np.array(disps)
    confs = np.array(confs)
    median_disp = float(np.median(disps))
    # Disagreement penalty: spread across zones reduces confidence.
    spread = float(np.std(disps)) if len(disps) > 1 else 0.0
    agreement = float(np.exp(-spread / 2.0))   # spread small -> ~1
    base_conf = float(np.mean(confs))
    # Near-focus bonus: if median disparity is near 0 AND zones agree, the
    # in-focus PSR drop should not penalise us -- bonus confidence.
    near_focus_bonus = 0.0
    if abs(median_disp) < 1.0 and agreement > 0.7:
        near_focus_bonus = 0.25 * (1.0 - abs(median_disp))
    composite_conf = min(1.0, base_conf * agreement + near_focus_bonus)
    return median_disp, composite_conf


def estimate_disparity(L: np.ndarray, R: np.ndarray,
                       max_disp_px: int = 32) -> tuple[float, float]:
    """Return (disparity_px, confidence).

    L, R: 2D strips (same shape). Disparity is measured along axis=1 (horizontal).
    Positive disparity means R is shifted to the right of L (back-focus convention).
    """
    # Collapse vertically to a 1D signal per view (sum within the AF zone).
    l = L.mean(axis=0).astype(np.float32)
    r = R.mean(axis=0).astype(np.float32)
    l = l - l.mean()
    r = r - r.mean()
    w = _window(len(l))
    l = l * w
    r = r * w

    n = len(l)
    nfft = 1 << (int(np.ceil(np.log2(2 * n))) )
    Lf = np.fft.rfft(l, nfft)
    Rf = np.fft.rfft(r, nfft)
    cross = Lf * np.conj(Rf)
    mag = np.abs(cross) + 1e-8
    # Standard cross-correlation (not pure phase-only) is more robust under noise
    # for low-texture scenes; weight slightly toward phase.
    cross = cross / (mag ** 0.5)
    corr = np.fft.irfft(cross, nfft)
    corr = np.concatenate([corr[-max_disp_px:], corr[:max_disp_px + 1]])
    # index 0 -> -max_disp, index max_disp -> 0, etc.
    peak_idx = int(np.argmax(corr))
    peak_val = float(corr[peak_idx])

    # Sub-pixel refinement via parabolic fit around the peak.
    if 0 < peak_idx < len(corr) - 1:
        a, b, c = corr[peak_idx - 1], corr[peak_idx], corr[peak_idx + 1]
        denom = (a - 2 * b + c)
        sub = 0.5 * (a - c) / denom if abs(denom) > 1e-9 else 0.0
    else:
        sub = 0.0
    disparity = (peak_idx - max_disp_px) + float(sub)

    # Confidence: peak-to-sidelobe ratio, excluding +/- 3 px around the peak.
    mask = np.ones_like(corr, dtype=bool)
    lo = max(0, peak_idx - 3)
    hi = min(len(corr), peak_idx + 4)
    mask[lo:hi] = False
    sidelobe = float(np.max(corr[mask])) if mask.any() else 1e-6
    psr = peak_val / max(sidelobe, 1e-6)
    # Squash to [0, 1]-ish for easy thresholding.
    confidence = float(np.tanh(0.5 * (psr - 1.0)))
    confidence = max(0.0, confidence)
    return disparity, confidence
