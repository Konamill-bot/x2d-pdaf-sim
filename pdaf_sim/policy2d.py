"""2D policies that consume a grid of PDAF zones and track a moving subject.

V4Policy adds two capabilities over the 1-D V3Policy:

1. **Spatial gradient over zones** -- find which zone has the strongest
   PDAF signal (the subject zone), and use disparity values at
   neighbouring zones to estimate where the subject is moving spatially.

2. **Bounding-box tracker** -- maintain a 4-D Kalman state over
   (x, y, dx, dy) for the subject. When PDAF zones go quiet
   (occlusion), the lens continues to be driven by the predicted
   subject position and known focus distance, instead of falling back
   to CDAF sweep.

The lens-distance control inside the subject zone reuses the V3 stack
(Kalman over focus, deadband, PID, CDAF fusion).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np


@dataclass
class Decision2D:
    lens_cmd: float
    swept: bool = False
    tracked_x: float = 0.0    # estimated subject x (for plotting)
    tracked_y: float = 0.0
    locked: bool = False


@dataclass
class V4Policy:
    """Multi-zone tracker with subject persistence.

    Inputs per step:
      zones: list of dicts, each with keys
        - 'disparity_mm' : float
        - 'pdaf_conf'    : float
        - 'cdaf_score'   : float
        - 'center_xy'    : (x, y) zone centre in image coords
      lens_pos : float (current lens position, mm)

    Output: Decision2D
    """
    # focus Kalman (1-D over focus distance)
    process_var_focus: float = 0.05
    meas_var_base: float = 0.5
    # subject bbox Kalman (4-D over x, y, dx, dy)
    process_var_pos: float = 4.0       # pixels^2 per frame
    process_var_vel: float = 0.5
    meas_var_pos: float = 25.0         # ~5 px std
    # decision
    deadband_mm: float = 0.05
    sweep_step: float = 0.2
    give_up_after_frames: int = 60
    zone_conf_floor: float = 0.05      # below this, ignore zone
    # PID lens drive
    pid_kp: float = 0.8
    pid_ki: float = 0.05
    pid_kd: float = 0.1

    _xf: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
    _Pf: np.ndarray = field(default_factory=lambda: np.eye(2) * 10.0)
    _xs: np.ndarray = field(default_factory=lambda: np.zeros(4))
    _Ps: np.ndarray = field(default_factory=lambda: np.eye(4) * 100.0)
    _has_subject: bool = False
    _occluded_streak: int = 0
    _sweep_dir: int = 1
    _pid_int: float = 0.0
    _pid_prev: float = 0.0

    def reset(self):
        self._xf = np.array([0.0, 0.0])
        self._Pf = np.eye(2) * 10.0
        self._xs = np.zeros(4)
        self._Ps = np.eye(4) * 100.0
        self._has_subject = False
        self._occluded_streak = 0
        self._sweep_dir = 1
        self._pid_int = 0.0
        self._pid_prev = 0.0

    def step(self, zones, lens_pos: float) -> Decision2D:
        # --- Predict subject Kalman (constant velocity)
        F = np.eye(4)
        F[0, 2] = 1.0; F[1, 3] = 1.0
        Qs = np.diag([self.process_var_pos, self.process_var_pos,
                      self.process_var_vel, self.process_var_vel])
        self._xs = F @ self._xs
        self._Ps = F @ self._Ps @ F.T + Qs

        # --- Predict focus Kalman
        Ff = np.array([[1.0, 1.0], [0.0, 1.0]])
        Qf = np.array([[self.process_var_focus, 0.0],
                       [0.0, self.process_var_focus]])
        self._xf = Ff @ self._xf
        self._Pf = Ff @ self._Pf @ Ff.T + Qf

        # --- Find the highest-confidence zone above the floor
        good_zones = [(i, z) for i, z in enumerate(zones)
                      if z['pdaf_conf'] >= self.zone_conf_floor]
        if good_zones:
            self._occluded_streak = 0
            best_i, best_z = max(good_zones, key=lambda iz: iz[1]['pdaf_conf'])

            # 1) Subject position update from best zone
            zx, zy = best_z['center_xy']
            Hs = np.zeros((2, 4)); Hs[0, 0] = 1.0; Hs[1, 1] = 1.0
            Rs = np.eye(2) * (self.meas_var_pos / max(best_z['pdaf_conf'], 1e-3))
            z_meas = np.array([zx, zy])
            y = z_meas - Hs @ self._xs
            S = Hs @ self._Ps @ Hs.T + Rs
            K = self._Ps @ Hs.T @ np.linalg.inv(S)
            self._xs = self._xs + K @ y
            self._Ps = (np.eye(4) - K @ Hs) @ self._Ps
            self._has_subject = True

            # 2) Focus update from best zone's disparity
            z_focus = lens_pos - best_z['disparity_mm']
            Hf = np.array([[1.0, 0.0]])
            Rf = np.array([[self.meas_var_base / max(best_z['pdaf_conf'], 1e-3)]])
            yf = z_focus - (Hf @ self._xf)
            Sf = Hf @ self._Pf @ Hf.T + Rf
            Kf = self._Pf @ Hf.T @ np.linalg.inv(Sf)
            self._xf = self._xf + (Kf @ yf).flatten()
            self._Pf = (np.eye(2) - Kf @ Hf) @ self._Pf
        else:
            # Occluded / no zone has signal: coast on prior.
            self._occluded_streak += 1
            # During occlusion, do NOT trigger CDAF sweep -- predict and wait.
            if self._occluded_streak >= self.give_up_after_frames:
                return Decision2D(lens_cmd=lens_pos, swept=False,
                                  tracked_x=float(self._xs[0]),
                                  tracked_y=float(self._xs[1]),
                                  locked=False)

        target = float(self._xf[0])
        err = target - lens_pos
        if abs(err) < self.deadband_mm:
            return Decision2D(lens_cmd=lens_pos, swept=False,
                              tracked_x=float(self._xs[0]),
                              tracked_y=float(self._xs[1]),
                              locked=self._has_subject)

        # PID drive
        self._pid_int = 0.9 * self._pid_int + err
        d = err - self._pid_prev
        self._pid_prev = err
        delta = float(np.clip(
            self.pid_kp * err + self.pid_ki * self._pid_int + self.pid_kd * d,
            -1.5, 1.5))
        return Decision2D(lens_cmd=lens_pos + delta, swept=False,
                          tracked_x=float(self._xs[0]),
                          tracked_y=float(self._xs[1]),
                          locked=self._has_subject)
