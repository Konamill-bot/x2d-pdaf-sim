# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""DualGated: a confidence-calibrated, innovation-gated, two-timescale Kalman AF-C policy.

Same interface as the run_x2d_plus policies: step(d, c, cs, lens) when a PDAF
measurement arrives (lens = lens position when it was exposed), coast() when
none does. Both return the lens command in mm. This is the Python reference
model for the C99 port in af_c/ (af_c/test_equiv.py checks them against each
other).

1. Calibrated confidence -> noise. The measurement variance is R = sigma(c)^2
   with sigma(c) = S0 * c^-P mm, fitted offline from the simulator's own PDAF
   stack (scripts/run_dual_gated.py, panel A). The baseline X2D+ uses
   R = 1/c, which is about 30x too wide at high confidence.
2. Confidence floor and innovation gate. Below C_FLOOR the measurement is not
   used. A measurement more than GATE sigmas from the prediction is held off;
   N_CONFIRM consecutive same-sign rejections are accepted as a real subject
   jump (re-initialise on it).
3. Two timescales. Both filters see the same accepted measurements:
     * agile  (q_hi): drives the lens while measurements arrive
     * smooth (q_lo): its long-horizon position and velocity take over the
       moment measurements stop (occlusion, dropout, low confidence)
   A single q cannot serve both jobs: a q agile enough for jittery motion
   fills the velocity estimate with measurement noise, and extrapolating that
   noise through a 1 s occlusion loses the subject (DEV_LOG, "DualGated").
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

S0, P = 0.036, 0.93      # sigma(c) = S0 * c^-P mm  (fit: scripts/run_dual_gated.py)


@dataclass
class DualGated:
    s0: float = S0
    p: float = P
    c_floor: float = 0.05        # confidence below this = no usable measurement
    gate: float = 3.5            # innovation gate, in sigmas
    n_confirm: int = 2           # consecutive same-sign gated-out measurements => real step
    q_hi: float = 1e-3           # white-acceleration noise, agile filter  (mm^2 / step^3)
    q_lo: float = 1e-5           # white-acceleration noise, smooth filter (mm^2 / step^3)
    lead: float = 3.0            # prediction lead (steps): pipeline latency
    pv0: float = 0.05 ** 2       # initial velocity variance
    sweep_step: float = 0.3      # search step when the track is lost (mm)
    lo: float = 0.0              # lens travel limits (mm)
    hi: float = 7.0
    handover_pos: bool = True    # on coast entry take the smooth position too, not just velocity
    # CDAF verification (opt-in; off = the validated policy). After every re-acquisition on a
    # new plane, the new lock is checked by contrast: PDAF can lock confidently on a false plane
    # (periodic texture), contrast cannot. A lock whose contrast falls below cdaf_ratio x the
    # contrast held before the jump starts a contrast hill-climb; PDAF resumes at its peak.
    cdaf_verify: bool = False
    cdaf_window: int = 30        # steps after a re-acquisition in which the lock is checked
    cdaf_settle: int = 6         # steps the new lock gets to settle before the check
    cdaf_ratio: float = 0.6
    cdaf_step: float = 0.5       # hill-climb step (mm)
    cdaf_max_steps: int = 8
    _xa: np.ndarray | None = None; _Pa: np.ndarray | None = None      # agile
    _xs: np.ndarray | None = None; _Ps: np.ndarray | None = None      # smooth
    _coasting: bool = False
    _low: int = 0; _dir: int = 1; _nrej: int = 0; _rsign: int = 0
    _cs_peak: float = 0.0; _pre_peak: float = 0.0; _since_jump: int = 1 << 30
    _climb: int = 0; _climb_pos: float = 0.0; _climb_start: float = 0.0; _climb_n: int = 0
    _climb_wait: int = 0; _best_lens: float = 0.0; _best_cs: float = -1.0; _reversed: bool = False

    @staticmethod
    def _pred(x, Pm, q):
        F = np.array([[1.0, 1.0], [0.0, 1.0]])
        return F @ x, F @ Pm @ F.T + q * np.array([[1 / 3, 1 / 2], [1 / 2, 1.0]])

    @staticmethod
    def _upd(x, Pm, z, R):
        S = Pm[0, 0] + R; K = Pm[:, 0] / S
        return x + K * (z - x[0]), Pm - np.outer(K, Pm[0, :])

    def _cmd(self):
        return float(np.clip(self._xa[0] + self._xa[1] * self.lead, self.lo, self.hi))

    def _init(self, z, R):
        self._xa = np.array([z, 0.0]); self._Pa = np.diag([R, self.pv0])
        self._xs = self._xa.copy(); self._Ps = self._Pa.copy()
        self._nrej = 0; self._coasting = False

    def _predict_both(self):
        self._xa, self._Pa = self._pred(self._xa, self._Pa, self.q_hi)
        self._xs, self._Ps = self._pred(self._xs, self._Ps, self.q_lo)

    def _enter_coast(self):
        if self._coasting:
            return
        if self.handover_pos:                       # long-horizon position and velocity
            self._xa = self._xs.copy(); self._Pa = self._Ps.copy()
        else:                                       # long-horizon velocity only
            self._xa = np.array([self._xa[0], self._xs[1]])
            self._Pa = self._Pa.copy(); self._Pa[1, 1] = self._Ps[1, 1]; self._Pa[0, 1] = self._Pa[1, 0] = 0.0
        self._coasting = True

    def step(self, d, c, cs, lens):
        if self._climb:
            return self._climb_step(c, cs, lens)
        z = lens - d
        R = (self.s0 * max(c, self.c_floor) ** -self.p) ** 2
        if self._xa is None:
            if c < self.c_floor:
                return float(lens)
            self._init(z, R); return self._cmd()
        if c < self.c_floor:                        # no usable measurement: coast
            self._enter_coast(); self._predict_both()
            self._low += 1
            if self._low >= 4 and self._Pa[0, 0] > 1.0:
                return self._sweep(lens)
            return self._cmd()
        self._predict_both()
        self._low = 0
        nu = z - self._xa[0]; S = self._Pa[0, 0] + R
        if nu * nu > self.gate ** 2 * S:            # gated out: keep the track
            s = 1 if nu > 0 else -1
            self._nrej = self._nrej + 1 if s == self._rsign else 1; self._rsign = s
            if self._nrej >= self.n_confirm:        # persistent => real step
                self._init(z, R)
                if self.cdaf_verify:                # verify the new plane by contrast
                    self._pre_peak = self._cs_peak; self._since_jump = 0
            return self._cmd()
        self._nrej = 0; self._coasting = False
        self._xa, self._Pa = self._upd(self._xa, self._Pa, z, R)
        self._xs, self._Ps = self._upd(self._xs, self._Ps, z, R)
        if self.cdaf_verify:
            if self._since_jump < self.cdaf_window:
                self._since_jump += 1
                if (self._since_jump >= self.cdaf_settle and self._pre_peak > 0
                        and cs < self.cdaf_ratio * self._pre_peak):
                    return self._start_climb(cs, lens)
            else:
                self._cs_peak = max(cs, self._cs_peak * 0.995)   # contrast held while tracking
        return self._cmd()

    def _start_climb(self, cs, lens):
        self._climb, self._climb_start, self._best_lens, self._best_cs = 1, lens, lens, cs
        self._climb_n, self._climb_wait, self._reversed = 0, 0, False
        self._since_jump = self.cdaf_window
        self._climb_pos = float(np.clip(lens + self.cdaf_step, self.lo, self.hi))
        return self._climb_pos

    def _climb_step(self, c, cs, lens):
        """One step of the contrast hill-climb, on the measurement exposed at `lens`."""
        self._climb_wait += 1
        if abs(lens - self._climb_pos) > 0.05 and self._climb_wait < 15:
            return self._climb_pos                  # that frame was exposed before the probe
        self._climb_wait = 0; self._climb_n += 1
        if cs > self._best_cs:
            self._best_cs, self._best_lens = cs, lens
            nxt = self._climb_pos + self._climb * self.cdaf_step
        elif not self._reversed and self._best_lens == self._climb_start:
            self._reversed = True; self._climb = -self._climb       # first probe went the wrong way
            nxt = self._climb_start + self._climb * self.cdaf_step
        else:
            return self._end_climb(c)
        if self._climb_n >= self.cdaf_max_steps or nxt < self.lo or nxt > self.hi:
            return self._end_climb(c)
        self._climb_pos = float(nxt)
        return self._climb_pos

    def _end_climb(self, c):
        self._climb = 0
        self._init(self._best_lens, (self.s0 * max(c, self.c_floor) ** -self.p) ** 2)
        return self._cmd()

    def predict(self):
        """Where the subject should be at the next measurement: (position mm, variance),
        without changing any state; None before the first track. While coasting this is
        the smooth filter's prediction: its uncertainty grows at the subject's long-horizon
        rate, not at the agile filter's."""
        if self._xa is None:
            return None
        if self._coasting:
            x, Pm = self._pred(self._xs, self._Ps, self.q_lo)
        else:
            x, Pm = self._pred(self._xa, self._Pa, self.q_hi)
        return float(x[0]), float(Pm[0, 0])

    def _sweep(self, lens):
        cmd = lens + self._dir * self.sweep_step
        if cmd > self.hi or cmd < self.lo:
            self._dir *= -1; cmd = lens + self._dir * self.sweep_step
        self._init(cmd, 4.0)
        return float(cmd)

    def coast(self):
        if self._climb:
            return self._climb_pos
        if self._xa is None:
            return 0.0
        self._enter_coast(); self._predict_both(); return self._cmd()
