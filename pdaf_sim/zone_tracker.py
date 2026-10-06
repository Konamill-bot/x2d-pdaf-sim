# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Per-zone PDAF estimates and a depth-only AF-T subject tracker (Python reference
model for af_c/af_track.c).

The AF-T configs in run_ablation_4phase use an IDEAL tracker: the simulator tells it
which AF zones the occluder covers. ZoneTracker has no such knowledge. It sees only
each zone's depth and confidence, plus the AF brain's prediction of where the subject
should be:

  * subject zones  : depth within GATE sigmas of the predicted subject position
  * occluder signal: in the SAME frame as visible subject zones, >= K_OCC other zones
                     that are NEARER than the subject, agree with each other, and form a
                     contiguous block at an edge of the AF area: something in front of the
                     subject moving into view. Once that
                     persists at one depth for OCC_CONFIRM frames, it starts the occluder
                     track.
                     An occluder only slightly nearer than the subject is found by segmenting
                     the frame itself: an edge block of zones clearly nearer than the rest.
  * occluder track : once seen, the occluder is tracked as a second object, and a zone
                     it explains (in front of the subject) is never taken as the subject.
                     A hidden subject's growing uncertainty therefore cannot swallow the
                     occluder.
  * subject hidden : fewer than M_MIN subject zones while the occluder track is alive:
                     hold (the brain coasts) for up to MAX_HOLD steps, then re-acquire
  * subject moved  : every zone disagrees with no occluder seen arriving (the whole AF
                     area changed at once): re-acquire immediately

The cue is physical: occluders are nearer, enter across the edge of the AF area, and cover
part of it before all of it. Its blind spots: an occluder that fills the whole AF area within
one frame looks like subject motion, so the tracker follows it; and an occluder whose depth
is within measurement noise of the subject's cannot be told apart from it.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .phase_corr import estimate_disparity

ZS0, ZP = 0.031, 1.58    # per-zone sigma(c) = ZS0 * c^-ZP mm (pooled fit, see DEV_LOG "ZoneTracker")


def zone_rows(z, h, n_zones):
    """Row slice of AF zone z -- the split estimate_disparity_multi_zone uses."""
    sh = max(1, h // n_zones); lo = z * sh
    return slice(lo, lo + sh if z < n_zones - 1 else h)


def zone_estimates(L, R, n_zones, max_disp_px):
    """Per-zone (disparity px, PSR confidence), exactly as inside estimate_disparity_multi_zone."""
    d = np.zeros(n_zones); c = np.zeros(n_zones)
    for z in range(n_zones):
        rs = zone_rows(z, L.shape[0], n_zones)
        d[z], c[z] = estimate_disparity(L[rs], R[rs], max_disp_px=max_disp_px)
    return d, c


def end_run(mask, side):
    """The contiguous run of `mask` starting at one end of the zone strip (0: first zone,
    1: last zone), as a boolean mask."""
    mask = np.asarray(mask, dtype=bool); n = len(mask); out = np.zeros(n, dtype=bool)
    idx = range(n) if side == 0 else range(n - 1, -1, -1)
    for i in idx:
        if not mask[i]:
            break
        out[i] = True
    return out


def edge_run(mask):
    """The zones of the longest contiguous run of `mask` that touches an end of the zone
    strip (zone 0 or the last zone), as a boolean mask."""
    mask = np.asarray(mask, dtype=bool); n = len(mask); out = np.zeros(n, dtype=bool)
    a = 0
    while a < n and mask[a]:
        a += 1
    b = n
    while b > 0 and mask[b - 1]:
        b -= 1
    if a >= n - b:
        out[:a] = True
    else:
        out[b:] = True
    return out


def combine_zones(disps, confs):
    """The combination step of estimate_disparity_multi_zone, on any subset of zones:
    median disparity (px) and agreement-weighted confidence."""
    disps = np.array(disps); confs = np.array(confs)
    median_disp = float(np.median(disps))
    spread = float(np.std(disps)) if len(disps) > 1 else 0.0
    agreement = float(np.exp(-spread / 2.0))
    base_conf = float(np.mean(confs))
    near_focus_bonus = 0.0
    if abs(median_disp) < 1.0 and agreement > 0.7:
        near_focus_bonus = 0.25 * (1.0 - abs(median_disp))
    return median_disp, min(1.0, base_conf * agreement + near_focus_bonus)


@dataclass
class ZoneTracker:
    s0: float = ZS0
    p: float = ZP
    c_floor: float = 0.05        # zones below this confidence are ignored
    gate: float = 3.5            # association gate, in sigmas
    m_min: int = 2               # subject zones needed to call the subject visible
    k_occ: int = 2               # nearer, mutually consistent foreign zones = occluder arriving
    occ_tol: float = 3.0         # foreign zones "agree" within occ_tol per-zone sigmas
    occ_confirm: int = 4         # consecutive frames a candidate occluder must persist
    occ_window: int = 30         # steps an unseen occluder track survives (0.5 s at 60 Hz)
    max_hold: int = 150          # steps to hold through full occlusion (2.5 s at 60 Hz)
    _occ_z: float = 0.0          # occluder track: depth (mm) and variance
    _occ_var: float = 0.0
    _occ_life: int = 0           # > 0 while the occluder track is alive
    _cand_z: float = 0.0         # occluder candidate: depth, its per-zone sigma, frames seen
    _cand_sig: float = 0.0
    _cand_n: int = 0
    _cand_side: int = 0          # which end of the zone strip the candidate covers (0 / 1)
    _hold: int = 0

    def _set_occluder(self, z, sig):
        self._occ_z = float(np.median(z))
        self._occ_var = float(np.median(sig)) ** 2 / len(z)
        self._occ_life = self.occ_window

    def select(self, z_mm, conf, pred):
        """z_mm: per-zone in-focus lens position (mm); conf: per-zone confidence;
        pred: (x, var) predicted subject position (mm) and its variance, or None if the
        brain has no track yet. Returns a boolean zone mask to measure from, or None
        when the subject is hidden and the brain should coast."""
        z_mm = np.asarray(z_mm, dtype=float); conf = np.asarray(conf, dtype=float)
        valid = conf >= self.c_floor
        if pred is None:
            self._occ_life = 0; self._hold = 0; self._cand_n = 0
            return valid
        x, var = pred
        sig = self.s0 * np.maximum(conf, self.c_floor) ** -self.p
        d_sub = (z_mm - x) ** 2 / (var + sig * sig)            # normalised distance to the subject
        subj = valid & (d_sub <= self.gate ** 2)
        occ = np.zeros_like(valid)
        if self._occ_life > 0:                                 # explain away: the known occluder first,
            d_occ = (z_mm - self._occ_z) ** 2 / (self._occ_var + sig * sig)
            occ = valid & (d_occ <= self.gate ** 2) & (z_mm > x)   # but only in front of the subject
            subj = subj & ~occ
            self._occ_life -= 1
        if np.count_nonzero(subj) >= self.m_min:
            # An occluder arriving = subject zones AND a nearer, self-consistent object in the
            # same frame (partial coverage). A subject that moves changes every zone at once,
            # so it never starts an occluder track.
            # A candidate must persist at the same depth for occ_confirm frames: a real
            # occluder slides in over several frames, per-zone noise does not repeat.
            # It must also be a contiguous block at an edge of the AF area: an opaque object
            # moving into view crosses the area's boundary first; rain lands on scattered zones.
            nearer = edge_run(valid & ~subj & (z_mm > x))     # nearer = larger lens extension
            if np.count_nonzero(nearer) < self.k_occ:
                # An occluder only slightly nearer than the subject falls inside the subject gate,
                # which is as wide as the PREDICTION's uncertainty. Zones measure each other far
                # more precisely, so segment within the frame: an edge block of subject-gated
                # zones that sits clearly nearer than the rest is a nearer object.
                # The block runs from an end of the strip while zones stay nearer than the
                # midpoint between that end zone and the median of the subject zones.
                med = float(np.median(z_mm[subj])); sg = float(np.median(sig[subj])); best = None
                for side, e in ((0, 0), (1, len(z_mm) - 1)):
                    if not subj[e] or z_mm[e] <= med:
                        continue
                    blk = end_run(subj & (z_mm > 0.5 * (z_mm[e] + med)), side)
                    rest = subj & ~blk
                    if np.count_nonzero(blk) >= self.k_occ and np.count_nonzero(rest) >= self.m_min:
                        gap = float(np.median(z_mm[blk]) - np.median(z_mm[rest]))
                        if gap > self.occ_tol * sg and (best is None or gap > best[0]):
                            best = (gap, blk, rest)
                if best is not None:
                    nearer, subj = best[1], best[2]
            if np.count_nonzero(occ) >= 1:
                self._set_occluder(z_mm[occ], sig[occ])
            elif np.count_nonzero(nearer) >= self.k_occ:
                zn = z_mm[nearer]; sn = sig[nearer]
                sm = float(np.median(sn)); zc = float(np.median(zn))
                if float(np.max(zn) - np.min(zn)) <= self.occ_tol * sm:
                    # frame to frame the object must stay at the same edge and hold its depth to
                    # its own precision (sigma / sqrt(n)): a real occluder does; rain lands on new
                    # zones at a new depth every frame
                    sz = sm / np.sqrt(len(zn)); side = 0 if nearer[0] else 1
                    same = (self._cand_n > 0 and side == self._cand_side
                            and abs(zc - self._cand_z) <= self.occ_tol * max(sz, self._cand_sig))
                    self._cand_n = self._cand_n + 1 if same else 1
                    self._cand_z, self._cand_sig, self._cand_side = zc, sz, side
                    if self._cand_n >= self.occ_confirm:
                        self._set_occluder(zn, sn); self._cand_n = 0
                else:
                    self._cand_n = 0
            else:
                self._cand_n = 0
            self._hold = 0
            return subj
        if self._occ_life > 0 and self._hold < self.max_hold:  # subject hidden behind the occluder
            if np.count_nonzero(occ) >= 1:
                # keep the occluder alive but FREEZE its depth: with the subject unseen,
                # updating it lets the occluder track drift onto the subject (track swap)
                self._occ_life = self.occ_window
            self._hold += 1
            return None
        self._hold = 0; self._occ_life = 0; self._cand_n = 0
        return valid                                           # subject moved: re-acquire
