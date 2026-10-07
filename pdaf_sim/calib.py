# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Online self-calibration of the PDAF conversion gain K (disparity px per mm of defocus).

On a real body K depends on the lens, aperture, focus position, image height and
temperature, so a factory table is only a starting point. The camera can check it with its
own lens moves: with the subject still, moving the lens by dL changes the measured disparity
by K * dL. KSelfCal keeps an exponentially weighted least-squares slope of those pairs.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass
class KSelfCal:
    k0: float                    # table value (possibly wrong)
    forget: float = 0.98         # per accepted pair
    min_move: float = 0.05       # mm of lens travel between the two exposures
    max_vel: float = 0.003       # mm/step: the brain must see the subject as ~still
    min_conf: float = 0.5
    _num: float = 0.0
    _den: float = 0.0
    _prev: tuple | None = None

    @property
    def k(self):
        if self._den < 0.05:                     # not enough evidence yet: trust the table
            return self.k0
        return float(np.clip(self._num / self._den, 0.5 * self.k0, 2.0 * self.k0))

    def update(self, lens, d_px, conf, subject_vel):
        """lens: lens position (mm) at exposure; d_px: measured disparity; subject_vel: the
        brain's velocity estimate (mm/step). Returns the current K estimate."""
        if conf < self.min_conf:
            self._prev = None
            return self.k
        if self._prev is not None and abs(subject_vel) <= self.max_vel:
            l0, d0 = self._prev; dl = lens - l0
            if abs(dl) >= self.min_move:
                self._num = self.forget * self._num + dl * (d_px - d0)
                self._den = self.forget * self._den + dl * dl
        self._prev = (lens, d_px)
        return self.k
