# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Subject-box AF (Python reference model for af_c/af_roi.c).

A detector gives the subject's box a few frames late, and the AF measures phase in ONE window
fitted to that box instead of in fixed AF cells, which a small subject cannot win against a
busy background (scripts/run_subject_classes.py). Two pieces:

  IspWindow : the window an ISP's phase-detection block actually measures for a requested
              box. Such blocks have rules: a minimum window size (enough PDAF samples, and
              room for the +-max_disp search), window edges on a coarse grid, inside the frame.
              The box is grown to the minimum size around its centre, moved inside the frame,
              and snapped outward to the grid. The defaults are generic placeholders, not any
              particular ISP's.
  RoiGate   : the ROI leads, depth vetoes. A measurement whose in-focus lens position
              disagrees with the AF brain's prediction (gate sigmas, per-zone noise model) is
              vetoed, at most max_veto - 1 frames in a row; then the detector is trusted.
"""
from __future__ import annotations
from dataclasses import dataclass
from .zone_tracker import ZS0, ZP


@dataclass
class IspWindow:
    align: int = 8       # window edges on multiples of this (px)
    min_w: int = 48      # minimum window width (px): twice the +-24 px disparity search
    min_h: int = 8       # minimum window height (px)

    def fit(self, y0, y1, x0, x1, width, height):
        """Rows y0..y1-1, columns x0..x1-1 of the requested box -> the measured window."""
        if x1 - x0 < self.min_w:
            x0 = (x0 + x1 - self.min_w) // 2; x1 = x0 + self.min_w
        if y1 - y0 < self.min_h:
            y0 = (y0 + y1 - self.min_h) // 2; y1 = y0 + self.min_h
        if x0 < 0: x1 -= x0; x0 = 0
        if x1 > width: x0 -= x1 - width; x1 = width
        if y0 < 0: y1 -= y0; y0 = 0
        if y1 > height: y0 -= y1 - height; y1 = height
        a = max(1, self.align)
        x0 = max(0, x0) // a * a; y0 = max(0, y0) // a * a
        x1 = min(width, (x1 + a - 1) // a * a); y1 = min(height, (y1 + a - 1) // a * a)
        return y0, y1, x0, x1


@dataclass
class RoiGate:
    gate: float = 3.5       # sigmas
    max_veto: int = 6       # the detector wins on the max_veto-th disagreeing frame in a row
    c_floor: float = 0.05
    _n: int = 0

    def check(self, z, conf, pred):
        """z: the measurement's in-focus lens position (mm); pred: the brain's (x, var), or
        None before it has a track. True = use the measurement; False = veto it this frame."""
        if pred is None:
            return True
        sig = ZS0 * max(conf, self.c_floor) ** -ZP
        if (z - pred[0]) ** 2 > self.gate ** 2 * (pred[1] + sig * sig):
            self._n += 1
            if self._n < self.max_veto:
                return False
        self._n = 0
        return True
