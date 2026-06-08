"""AF decision policies.

A policy decides, given a (disparity, confidence) measurement, where to move
the lens this frame. We compare two:

  StatelessPolicy  : if confidence > tau, jump to PDAF estimate; else CDAF sweep.
                     This is roughly what current bodies do per-frame with no memory.

  TemporalPolicy   : a 1-D Kalman filter over lens position (and velocity)
                     fuses each PDAF measurement weighted by its confidence.
                     Low-confidence frames coast on the prior instead of
                     triggering a CDAF sweep -- which is the visible "hunting".

A "hunting event" is counted whenever the policy commands a CDAF sweep
(stateless) OR whenever the commanded lens position oscillates by more than
HUNT_THRESHOLD px between consecutive frames without the subject moving.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

HUNT_THRESHOLD = 3.0  # px of focus-position oscillation considered "hunting"


@dataclass
class Decision:
    lens_cmd: float        # commanded lens position (defocus units, px-equivalent)
    swept: bool = False    # did we trigger a CDAF sweep this frame?


@dataclass
class StatelessPolicy:
    tau: float = 0.35
    sweep_step: float = 5.0
    _last_cmd: float = 0.0
    _sweep_dir: int = 1

    def step(self, disparity: float, confidence: float, lens_pos: float) -> Decision:
        if confidence >= self.tau:
            cmd = lens_pos - disparity  # drive toward zero disparity
            self._last_cmd = cmd
            return Decision(lens_cmd=cmd, swept=False)
        # Low confidence -> CDAF hill-climb: step in current direction.
        cmd = lens_pos + self._sweep_dir * self.sweep_step
        self._sweep_dir *= -1  # naive bidirectional sweep
        return Decision(lens_cmd=cmd, swept=True)


@dataclass
class TemporalPolicy:
    """Kalman filter over (focus_position, focus_velocity).

    Measurement = lens_pos - disparity (the PDAF-implied focus target).
    Measurement variance scales with 1/confidence so low-confidence frames
    barely perturb the state -- the lens coasts instead of hunting.
    """
    tau_sweep: float = 0.08              # only sweep below THIS confidence
    process_var: float = 0.5
    meas_var_base: float = 1.0
    sweep_step: float = 5.0
    _x: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
    _P: np.ndarray = field(default_factory=lambda: np.eye(2) * 10.0)
    _sweep_dir: int = 1

    def step(self, disparity: float, confidence: float, lens_pos: float) -> Decision:
        # Predict
        F = np.array([[1.0, 1.0], [0.0, 1.0]])
        Q = np.array([[self.process_var, 0.0], [0.0, self.process_var]])
        self._x = F @ self._x
        self._P = F @ self._P @ F.T + Q

        if confidence < self.tau_sweep:
            # Truly ambiguous -- give up and CDAF sweep this frame.
            cmd = lens_pos + self._sweep_dir * self.sweep_step
            self._sweep_dir *= -1
            return Decision(lens_cmd=cmd, swept=True)

        # Update with PDAF measurement, weighted by confidence.
        z = lens_pos - disparity
        H = np.array([[1.0, 0.0]])
        R = np.array([[self.meas_var_base / max(confidence, 1e-3)]])
        y = z - (H @ self._x)
        S = H @ self._P @ H.T + R
        K = self._P @ H.T @ np.linalg.inv(S)
        self._x = self._x + (K @ y).flatten()
        self._P = (np.eye(2) - K @ H) @ self._P

        return Decision(lens_cmd=float(self._x[0]), swept=False)


@dataclass
class BehavioralPolicy:
    """Parameterized policy designed to be FIT to observed real-camera behavior.

    Unifies the stateless and temporal cases in a single tunable family so
    scipy.optimize can search over parameters that minimize the gap between
    simulated lens trajectory and an observed (e.g. GFX 100 II) trajectory.

    Parameters
    ----------
    tau_trust   : >=this confidence -> trust PDAF fully (snap)
    tau_sweep   : <this confidence -> CDAF sweep
    (between)   : Kalman update with confidence-weighted variance
    process_var : Kalman process noise (~ subject-motion bandwidth)
    meas_var_base : measurement noise floor (1/conf scaling on top)
    sweep_step  : CDAF step size (mm)
    snap_gain   : 0..1, how aggressively to jump to PDAF when trusted
                  (real bodies often damp this for smoothness)
    reset_on_shot : if True, drop Kalman state after a shutter event
                    (this is the X2D behaviour you noticed)
    """
    tau_trust: float = 0.5
    tau_sweep: float = 0.08
    process_var: float = 0.5
    meas_var_base: float = 1.0
    sweep_step: float = 0.2
    snap_gain: float = 1.0
    reset_on_shot: bool = False
    # Hard timeout: stop sweeping after this many consecutive low-confidence
    # frames and report 'focus failed' (Sony A7 IV does this in ~0.7s on
    # featureless white scenes -> purple-box failure indicator).
    give_up_after_frames: int = 60

    _x: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
    _P: np.ndarray = field(default_factory=lambda: np.eye(2) * 10.0)
    _sweep_dir: int = 1
    _low_conf_streak: int = 0
    _given_up: bool = False

    def reset(self):
        self._x = np.array([0.0, 0.0])
        self._P = np.eye(2) * 10.0
        self._sweep_dir = 1
        self._low_conf_streak = 0
        self._given_up = False

    def on_shutter(self):
        if self.reset_on_shot:
            self.reset()

    def step(self, disparity: float, confidence: float, lens_pos: float) -> Decision:
        F = np.array([[1.0, 1.0], [0.0, 1.0]])
        Q = np.array([[self.process_var, 0.0], [0.0, self.process_var]])
        self._x = F @ self._x
        self._P = F @ self._P @ F.T + Q

        if confidence < self.tau_sweep:
            self._low_conf_streak += 1
            if self._low_conf_streak >= self.give_up_after_frames:
                # Honest failure: hold position, signal focus failed.
                self._given_up = True
                return Decision(lens_cmd=lens_pos, swept=False)
            cmd = lens_pos + self._sweep_dir * self.sweep_step
            self._sweep_dir *= -1
            return Decision(lens_cmd=cmd, swept=True)
        self._low_conf_streak = 0
        self._given_up = False

        z = lens_pos - disparity
        H = np.array([[1.0, 0.0]])
        R = np.array([[self.meas_var_base / max(confidence, 1e-3)]])
        y = z - (H @ self._x)
        S = H @ self._P @ H.T + R
        K = self._P @ H.T @ np.linalg.inv(S)
        self._x = self._x + (K @ y).flatten()
        self._P = (np.eye(2) - K @ H) @ self._P

        if confidence >= self.tau_trust:
            # High-confidence snap: blend Kalman state with raw measurement.
            cmd = (1 - self.snap_gain) * float(self._x[0]) + self.snap_gain * z
        else:
            cmd = float(self._x[0])
        return Decision(lens_cmd=cmd, swept=False)

    # --- (de)serialize so we can save fit results -----------------------
    def as_params(self) -> dict:
        return {
            'tau_trust': self.tau_trust,
            'tau_sweep': self.tau_sweep,
            'process_var': self.process_var,
            'meas_var_base': self.meas_var_base,
            'sweep_step': self.sweep_step,
            'snap_gain': self.snap_gain,
            'reset_on_shot': self.reset_on_shot,
            'give_up_after_frames': self.give_up_after_frames,
        }

    @classmethod
    def from_params(cls, p: dict) -> 'BehavioralPolicy':
        return cls(**p)


@dataclass
class V3Policy:
    """v3 stack: Temporal Kalman + deadband + PID lens drive +
    PDAF/CDAF fusion + sticky lock.

    Inputs per step:
      disparity     -- PDAF-implied lens error (mm)
      pdaf_conf     -- 0..1 confidence of the PDAF disparity
      cdaf_score    -- contrast-detection sharpness (higher = sharper)
      lens_pos      -- current lens position (mm)

    Outputs:
      Decision(lens_cmd, swept)

    Notes
    -----
    * Deadband: if estimated remaining error is within deadband_mm,
      don't move the lens. Kills the infinite-tiny-correction loop.
    * PID: instead of snapping to target, command a PID-controlled
      motion. Damps overshoot at high update rates.
    * Fusion: scene-conditional weighting between PDAF (good far from
      focus, weak near focus due to PSR drop) and CDAF (good near
      focus, weak far from focus because gradient is flat). The
      fused signal estimates "true" defocus more robustly than either
      alone.
    """
    # decision thresholds
    tau_sweep: float = 0.05
    deadband_mm: float = 0.05
    give_up_after_frames: int = 60
    sweep_step: float = 0.2

    # Kalman over (focus_pos, focus_vel)
    process_var: float = 0.05
    meas_var_base: float = 0.5

    # PID on lens commanded position (acting on target - actual)
    pid_kp: float = 0.8
    pid_ki: float = 0.05
    pid_kd: float = 0.1

    # Fusion: CDAF weight grows as PDAF conf drops AND cdaf gradient
    # is meaningful. Gated by minimum |gradient| to avoid following noise
    # in low-contrast scenes where CDAF itself is unreliable.
    cdaf_blend_max: float = 0.3
    cdaf_grad_min: float = 1e-3        # below this, CDAF signal is noise
    cdaf_step_mm: float = 0.1          # max nudge per frame from CDAF

    _x: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
    _P: np.ndarray = field(default_factory=lambda: np.eye(2) * 10.0)
    _sweep_dir: int = 1
    _low_conf_streak: int = 0
    _pid_int: float = 0.0
    _pid_prev_err: float = 0.0
    _cdaf_prev: float | None = None
    _cdaf_lens_prev: float = 0.0

    def reset(self):
        self._x = np.array([0.0, 0.0])
        self._P = np.eye(2) * 10.0
        self._sweep_dir = 1
        self._low_conf_streak = 0
        self._pid_int = 0.0
        self._pid_prev_err = 0.0
        self._cdaf_prev = None
        self._cdaf_lens_prev = 0.0

    def on_shutter(self):
        pass  # preserve state, matches user's X2D T2 observation

    def step(self, disparity: float, pdaf_conf: float,
             cdaf_score: float, lens_pos: float) -> Decision:
        # --- Estimate dCDAF / dlens since last step (sign tells gradient dir)
        if self._cdaf_prev is not None and abs(lens_pos - self._cdaf_lens_prev) > 1e-6:
            cdaf_grad = (cdaf_score - self._cdaf_prev) / (lens_pos - self._cdaf_lens_prev)
        else:
            cdaf_grad = 0.0
        self._cdaf_prev = cdaf_score
        self._cdaf_lens_prev = lens_pos

        # --- Predict Kalman
        F = np.array([[1.0, 1.0], [0.0, 1.0]])
        Q = np.array([[self.process_var, 0.0], [0.0, self.process_var]])
        self._x = F @ self._x
        self._P = F @ self._P @ F.T + Q

        if pdaf_conf < self.tau_sweep and abs(cdaf_grad) < 1e-4:
            # Both signals unusable -> CDAF blind sweep (or give up)
            self._low_conf_streak += 1
            if self._low_conf_streak >= self.give_up_after_frames:
                return Decision(lens_cmd=lens_pos, swept=False)
            cmd = lens_pos + self._sweep_dir * self.sweep_step
            self._sweep_dir *= -1
            return Decision(lens_cmd=cmd, swept=True)
        self._low_conf_streak = 0

        # --- Update Kalman with PDAF measurement
        z_pdaf = lens_pos - disparity
        H = np.array([[1.0, 0.0]])
        R = np.array([[self.meas_var_base / max(pdaf_conf, 1e-3)]])
        y = z_pdaf - (H @ self._x)
        S = H @ self._P @ H.T + R
        K = self._P @ H.T @ np.linalg.inv(S)
        self._x = self._x + (K @ y).flatten()
        self._P = (np.eye(2) - K @ H) @ self._P

        target_pdaf = float(self._x[0])

        # --- Fuse with CDAF: nudge target by gradient ascent ONLY when
        # CDAF gradient is meaningfully above noise floor. Otherwise the
        # gradient sign is random and following it wastes lens travel.
        cdaf_weight = min(self.cdaf_blend_max, max(0.0, 1.0 - pdaf_conf))
        if abs(cdaf_grad) > self.cdaf_grad_min and cdaf_weight > 0:
            target_cdaf = lens_pos + self.cdaf_step_mm * np.sign(cdaf_grad)
            target = (1 - cdaf_weight) * target_pdaf + cdaf_weight * target_cdaf
        else:
            target = target_pdaf

        # --- Deadband: don't bother moving if error is tiny
        err = target - lens_pos
        if abs(err) < self.deadband_mm:
            return Decision(lens_cmd=lens_pos, swept=False)

        # --- PID drive on the error
        self._pid_int = 0.9 * self._pid_int + err   # leaky integrator
        d_err = err - self._pid_prev_err
        self._pid_prev_err = err
        delta = (self.pid_kp * err
                 + self.pid_ki * self._pid_int
                 + self.pid_kd * d_err)
        # Saturate per-step motion to avoid wild commands
        delta = float(np.clip(delta, -1.5, 1.5))
        return Decision(lens_cmd=lens_pos + delta, swept=False)


@dataclass
class CompositePolicy:
    """v2 policy fixing the three Layer-1 failure modes catalogued in FINDINGS.md:

    1. Near-focus PSR confidence drop -> composite confidence (caller passes
       multi-zone + near-focus-bonus confidence; this policy trusts it).
    2. Stateless single-frame stochasticity -> Kalman temporal prior + 3-frame
       confidence accumulator (require N high-conf frames before snapping).
    3. Conservative tau_trust -> lower trust threshold but compensate with
       agreement-based confidence so trust is earned via consistency, not
       a single noisy reading.

    The policy is intentionally "sticky in focus": once it commits to a
    focus position with several high-conf frames, a single low-conf frame
    does NOT trigger sweep. This prevents the X2D-style "near-focus failure".
    """
    tau_trust: float = 0.30
    tau_sweep: float = 0.05
    process_var: float = 0.2
    meas_var_base: float = 0.5
    sweep_step: float = 0.2
    accumulate_frames: int = 3       # high-conf frames needed before trusting
    sticky_grace_frames: int = 8     # tolerate this many low-conf frames before sweep
    give_up_after_frames: int = 60

    _x: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
    _P: np.ndarray = field(default_factory=lambda: np.eye(2) * 10.0)
    _sweep_dir: int = 1
    _high_conf_streak: int = 0
    _low_conf_streak: int = 0
    _locked: bool = False

    def reset(self):
        self._x = np.array([0.0, 0.0])
        self._P = np.eye(2) * 10.0
        self._sweep_dir = 1
        self._high_conf_streak = 0
        self._low_conf_streak = 0
        self._locked = False

    def on_shutter(self):
        pass  # state IS preserved across shutter (matches Sony A7 IV, X2D T2)

    def step(self, disparity: float, confidence: float, lens_pos: float) -> Decision:
        F = np.array([[1.0, 1.0], [0.0, 1.0]])
        Q = np.array([[self.process_var, 0.0], [0.0, self.process_var]])
        self._x = F @ self._x
        self._P = F @ self._P @ F.T + Q

        if confidence < self.tau_sweep:
            self._low_conf_streak += 1
            self._high_conf_streak = 0
            # STICKY: if we're locked, tolerate brief low-conf without sweeping.
            if self._locked and self._low_conf_streak < self.sticky_grace_frames:
                return Decision(lens_cmd=lens_pos, swept=False)
            self._locked = False
            if self._low_conf_streak >= self.give_up_after_frames:
                return Decision(lens_cmd=lens_pos, swept=False)
            cmd = lens_pos + self._sweep_dir * self.sweep_step
            self._sweep_dir *= -1
            return Decision(lens_cmd=cmd, swept=True)

        self._low_conf_streak = 0

        z = lens_pos - disparity
        H = np.array([[1.0, 0.0]])
        R = np.array([[self.meas_var_base / max(confidence, 1e-3)]])
        y = z - (H @ self._x)
        S = H @ self._P @ H.T + R
        K = self._P @ H.T @ np.linalg.inv(S)
        self._x = self._x + (K @ y).flatten()
        self._P = (np.eye(2) - K @ H) @ self._P

        if confidence >= self.tau_trust:
            self._high_conf_streak += 1
            if self._high_conf_streak >= self.accumulate_frames:
                self._locked = True
        return Decision(lens_cmd=float(self._x[0]), swept=False)
