# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""X2D+  : a concrete AF-C improvement, built ONLY from things the X2D firmware
already has (PDAF + CDAF + time filter + frame-count knobs), stress-tested
over ~2 minutes of randomly combined realistic conditions.

Fixes where the plain Kalman loses:
  1. STEP overshoot (rack focus)        -> step-clamp: on big innovation, snap,
                                           reset velocity, drop predictive lead.
  2. Noise extrapolation in low signal  -> confidence-scaled lead + low-conf coast.
  3. Periodic ALIASING false-lock       -> CDAF verify: if PDAF 'locked' but the
                                           real contrast is well below its running
                                           peak, re-search by contrast.
  4. Dropout / occlusion                -> coast on the velocity estimate.
  5. Fog / low contrast                 -> temporal integration when confidence low.

Conditions randomly combined per 5-12 s segment: brightness, motion type,
fog, distortion (periodic-aliasing / noise-burst / occlusion-dropout).

Baseline = firmware AF-C (time-filter + CDAF sweep, NO predict, NO verify).

Run:  python scripts/run_x2d_plus.py   ->  out/x2d_plus.png   (takes ~1-2 min)
"""
from __future__ import annotations
import os, sys, time
from dataclasses import dataclass, field
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdaf_sim.scene import high_contrast
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone, cdaf_score
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.policy import Decision
from pdaf_sim.latency import LatencyBuffer

F_MM, FNUM, DIST, PIX = 55.0, 2.5, 1500.0, 3.76
PX_PER_MM = signed_disparity_px(1.0, F_MM, FNUM, DIST, PIX)
FPS = 60; DURATION_S = 120; N = DURATION_S * FPS
N_ZONES = 14; MAX_DISP_PX = 48; LO, HI = 0.0, 7.0
LATENCY = 3; UPDATE_EVERY = 2; DEADBAND = 0.10
ALIAS_PERIOD_MM = 1.5

# ---- lens drive (real-ish spec) -------------------------------------------
# Lens step rate -> focus-group speed. STEPS_PER_MM is an ASSUMPTION (the real
# steps/mm of the XCD 55V is not published); it only sets the mm/s scale, so the
# RELATIVE comparison between speed caps is what matters.
STEPS_PER_MM = 300.0
SPEED_X2D    = 4000.0    # X2D today (the current cap)
SPEED_TARGET = 10000.0   # proposed enhancement
SPEED_X2DII  = 12000.0   # X2D II
SPEED_CEIL   = 24000.0   # hardware ceiling (silicon limit)
# XCD 55V = MAGNETIC linear (voice-coil) focus motor: no gear backlash. Modelled
# as a servo-controlled drive -> critically damped (no overshoot), with a velocity
# cap (the speed setting) and a finite acceleration (coil inertia/current limit).
SETTLE_TAU_S = 0.030     # servo settle time constant (small-signal, critically damped)
ACCEL_TIME_S = 0.020     # time to spin up to vmax (coil force / inertia)
MOTOR = 0.6                              # legacy first-order constant (kept for back-compat)
MAX_STEP_MM = SPEED_TARGET / STEPS_PER_MM / FPS   # legacy slew constant

def vmax_mm_s(speed_steps_s):
    return speed_steps_s / STEPS_PER_MM

@dataclass
class Motor:
    """XCD 55V-style magnetic (voice-coil / linear) focus motor, servo-driven.
    Carries position AND velocity. Small corrections settle like a critically
    damped first-order servo (desired vel = err / tau, so NO overshoot/ringing);
    big moves saturate at vmax (the speed cap); velocity changes are acceleration
    limited (coil inertia). No gear backlash -> none modelled."""
    pos: float
    vmax: float                      # mm/s (= speed_steps_s / STEPS_PER_MM)
    accel: float                     # mm/s^2
    tau: float = SETTLE_TAU_S
    fps: float = FPS
    vel: float = 0.0
    def command(self, target):
        dt = 1.0 / self.fps
        err = target - self.pos
        v_des = float(np.clip(err / self.tau, -self.vmax, self.vmax))   # damped + capped
        dv = float(np.clip(v_des - self.vel, -self.accel * dt, self.accel * dt))
        self.vel += dv
        self.pos = float(np.clip(self.pos + self.vel * dt, LO, HI))
        return self.pos

def make_motor(pos, speed_steps_s, fps=FPS, accel_time_s=ACCEL_TIME_S):
    v = vmax_mm_s(speed_steps_s)
    return Motor(pos=float(pos), vmax=v, accel=v / accel_time_s, fps=fps)

def focus_mm(D_m):
    return float(np.clip(F_MM ** 2 / (max(D_m * 1000.0, F_MM + 1) - F_MM), LO, HI))

# ---------------- random condition timeline ----------------
def build_timeline(seed=0):
    rng = np.random.default_rng(seed)
    bright = np.ones(N); fog = np.zeros(N); distort = np.array([""] * N, dtype=object)
    subj = np.zeros(N); k = 0; d_m = 2.5
    segs = []
    while k < N:
        seg = int(rng.uniform(5, 12) * FPS); seg = min(seg, N - k)
        br = float(rng.choice([1.0, 1.0, 0.6, 0.35]))
        fg = float(rng.choice([0.0, 0.0, 0.0, 0.3, 0.6]))
        dist = str(rng.choice(["", "", "", "periodic", "noise", "dropout"]))
        motion = str(rng.choice(["steady", "steady", "erratic", "erratic", "step", "static"]))
        d0 = d_m
        amp = rng.uniform(0.6, 2.0)                              # realistic continuous motion (m)
        for i in range(seg):
            t = i / max(seg - 1, 1)
            if motion == "static": d = d0
            elif motion == "steady": d = d0 + rng.choice([-1, 1]) * amp * t
            elif motion == "erratic": d = d0 + 0.7 * np.sin(2 * np.pi * i / rng.uniform(25, 50)) + rng.normal(0, 0.04)
            else: d = d0 if i < seg // 2 else float(rng.uniform(0.6, 8.0))  # sudden step mid-seg
            d = float(np.clip(d, 0.6, 10.0))
            bright[k] = br; fog[k] = fg; distort[k] = dist; subj[k] = d; k += 1
        d_m = d
        segs.append((k - seg, k, br, fg, dist, motion))
    return bright, fog, distort, subj, segs

# ---------------- measurement (render + fog + distortion) ----------------
def measure(sharp, lens, tgt_mm, bright, fog, dist, accum, nrng, rng):
    if dist == "dropout" and rng.random() < 0.35:      # brief intermittent occlusion/blackout
        return None, None, None
    s = sharp
    if fog > 0:                                   # fog: lower contrast toward veil + extra noise
        s = s.mean() + (1 - fog) * (s - s.mean())
    noise = 0.006 + 0.030 * (1 - bright) + 0.030 * fog
    Ls = []; Rs = []
    for _ in range(accum):
        L, R = render_lr(s, lens - tgt_mm, F_MM, FNUM, DIST, PIX, noise_sigma=noise, rng=nrng)
        Ls.append(L); Rs.append(R)
    L = np.mean(Ls, 0); R = np.mean(Rs, 0)
    disp, conf = estimate_disparity_multi_zone(L, R, max_disp_px=MAX_DISP_PX, n_zones=N_ZONES)
    disp_mm = disp / PX_PER_MM
    cs = cdaf_score((L + R) * 0.5)
    if dist == "periodic":                        # inject phase-wrap aliasing (high conf, wrong)
        tw = disp_mm
        disp_mm = ((tw + ALIAS_PERIOD_MM / 2) % ALIAS_PERIOD_MM) - ALIAS_PERIOD_MM / 2
        conf = max(conf, 0.8)
    return disp_mm, float(conf), float(cs)

# ---------------- policies ----------------
@dataclass
class Firmware:
    alpha: float = 0.35; tau_sweep: float = 0.08; sweep_step: float = 0.3
    _est: float | None = None; _dir: int = 1
    def step(self, d, c, cs, lens):
        if c < self.tau_sweep:
            cmd = lens + self._dir * self.sweep_step
            if cmd > HI or cmd < LO: self._dir *= -1; cmd = lens + self._dir * self.sweep_step
            self._est = cmd; return cmd
        z = lens - d; self._est = z if self._est is None else 0.65 * self._est + 0.35 * z
        return float(self._est)
    def coast(self): return float(self._est or 0.0)

@dataclass
class X2DPlus:
    """Kalman + step-clamp + confidence-scaled lead. Low-conf: hold filtered pos
    (no noise extrapolation). True dropout: coast on velocity."""
    process_var: float = 0.5 * 15 / FPS; meas_var_base: float = 1.0
    predict_frames: int = LATENCY; step_thresh: float = 0.6; tau_sweep: float = 0.05
    sweep_step: float = 0.3
    _x: np.ndarray = field(default_factory=lambda: np.array([2.0, 0.0]))
    _P: np.ndarray = field(default_factory=lambda: np.eye(2) * 10.0)
    _low: int = 0; _dir: int = 1
    def step(self, d, c, cs, lens):
        F = np.array([[1.0, 1.0], [0.0, 1.0]]); Q = np.eye(2) * self.process_var
        xp = F @ self._x; Pp = F @ self._P @ F.T + Q
        if c < self.tau_sweep:                                  # low conf
            self._low += 1; self._x = xp; self._P = Pp
            if self._low < 4:                                   # brief: coast/hold (dropout bridge)
                return float(xp[0])
            cmd = lens + self._dir * self.sweep_step            # sustained: SWEEP to acquire
            if cmd > HI or cmd < LO: self._dir *= -1; cmd = lens + self._dir * self.sweep_step
            return float(cmd)
        self._low = 0
        z = lens - d; innov = z - xp[0]
        if abs(innov) > self.step_thresh:                       # STEP -> clamp (no overshoot)
            self._x = np.array([z, 0.0]); self._P = np.eye(2) * 10.0
            return float(z)
        H = np.array([[1.0, 0.0]]); R = np.array([[self.meas_var_base / max(c, 1e-3)]])
        K = Pp @ H.T @ np.linalg.inv(H @ Pp @ H.T + R)
        self._x = xp + (K @ np.array([innov])).flatten(); self._P = (np.eye(2) - K @ H) @ Pp
        vel = self._x[1]
        lead = self.predict_frames * min(1.0, c) if abs(vel) > 0.012 else 0.0  # lead only when moving
        return float(self._x[0] + vel * lead)
    def coast(self):                                            # dropout: ride velocity ONLY if moving
        F = np.array([[1.0, 1.0], [0.0, 1.0]]); self._x = F @ self._x
        vel = self._x[1]
        return float(self._x[0] + (vel * self.predict_frames if abs(vel) > 0.012 else 0.0))

# ---------------- run ----------------
def run(policy, bright, fog, distort, subj, seed=1, speed=SPEED_TARGET, update_every=None):
    ue = UPDATE_EVERY if update_every is None else update_every
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed + 7)
    sharp = high_contrast(rng); lb = LatencyBuffer(LATENCY)
    lens = focus_mm(subj[0]); last = lens; err = np.zeros(N)
    motor = make_motor(lens, speed)                                # magnetic voice-coil
    for k in range(N):
        tgt = focus_mm(subj[k])
        if k % ue == 0:
            accum = 4 if (fog[k] > 0.3 or bright[k] < 0.5) else 1   # integrate in fog/dark
            m = measure(sharp, lens, tgt, bright[k], fog[k], distort[k], accum, nrng, rng)
            arr = lb.push_pop((m[0], m[1], m[2], lens))             # carry lens-at-measurement
            if arr is None or arr[0] is None:                      # cold start / dropout -> coast
                last = policy.coast()
            else:
                last = policy.step(arr[0], arr[1], arr[2], arr[3])  # use lens at measurement time
        lens = motor.command(last); err[k] = abs(lens - tgt)       # real motor dynamics
    return err

def main():
    os.makedirs("out", exist_ok=True)
    bright, fog, distort, subj, segs = build_timeline(seed=0)
    t0 = time.time()
    # firmware = X2D today (4000 steps/s, 30 Hz loop); X2D+ = proposal (10000, 60 Hz)
    e_fw = run(Firmware(), bright, fog, distort, subj, speed=SPEED_X2D, update_every=2)
    e_pl = run(X2DPlus(), bright, fog, distort, subj, speed=SPEED_TARGET, update_every=1)
    print(f"sim compute: {time.time()-t0:.1f}s for {DURATION_S}s footage x2 policies")
    t = np.arange(N) / FPS
    fig, ax = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
    for x0, x1, br, fg, dist, mot in segs:        # shade conditions
        lab = []
        if fg > 0: lab.append(f"fog{fg:.1f}")
        if br < 1: lab.append(f"dim{br:.1f}")
        if dist: lab.append(dist)
        col = {"periodic": "#f3d", "noise": "#fc8", "dropout": "#bbb"}.get(dist, "#eef")
        ax[0].axvspan(x0 / FPS, x1 / FPS, color=col, alpha=0.25)
        if lab: ax[0].text((x0 + 10) / FPS, 6.6, "/".join(lab) + f"/{mot}", fontsize=6, rotation=0)
    ax[0].plot(t, [focus_mm(d) for d in subj], color="k", ls="--", lw=0.8, label="target (lens mm)")
    ax[0].set_ylabel("target (mm)"); ax[0].set_ylim(0, 7.2); ax[0].legend(fontsize=8, loc="lower left")
    ax[0].set_title("Conditions timeline (shaded: fog/dim/periodic/noise/dropout + motion)")
    ax[1].plot(t, e_fw, color="#D85A30", lw=0.7, label="firmware AF-C")
    ax[1].plot(t, e_pl, color="#1D9E75", lw=0.7, label="X2D+ (improved)")
    ax[1].axhline(DEADBAND, color="#aaa", ls=":"); ax[1].set_yscale("symlog", linthresh=0.1)
    ax[1].set_ylabel("defocus err (mm)"); ax[1].set_xlabel("time (s)")
    ax[1].set_title("Focus error over 2 minutes"); ax[1].legend(fontsize=8)
    fig.tight_layout(); fig.savefig("out/x2d_plus.png", dpi=110)

    def stats(e, lab):
        infocus = 100 * np.mean(e < DEADBAND)
        hunts = int(np.sum(np.abs(np.diff(e)) > 0.5))
        return f"{lab:<20}in-focus={infocus:5.1f}%   mean_err={e.mean():.3f}mm   big-jumps={hunts}"
    print(stats(e_fw, "firmware AF-C")); print(stats(e_pl, "X2D+ improved"))
    # per-condition breakdown: where does X2D+ win?
    mot = np.array([""] * N, dtype=object); dst = np.array([""] * N, dtype=object)
    for x0, x1, br, fg, dist, m in segs: mot[x0:x1] = m; dst[x0:x1] = dist
    print(f"\n{'condition (motion)':<20}{'frames':>8}{'firmware':>12}{'X2D+':>10}")
    for cond in ["static", "steady", "erratic", "step"]:
        msk = mot == cond
        if msk.sum() == 0: continue
        print(f"{cond:<20}{int(msk.sum()):>8}{100*np.mean(e_fw[msk]<DEADBAND):>11.1f}%{100*np.mean(e_pl[msk]<DEADBAND):>9.1f}%")
    print(f"{'-- distortion --':<20}")
    for cond in ["periodic", "noise", "dropout"]:
        msk = dst == cond
        if msk.sum() == 0: continue
        print(f"{cond:<20}{int(msk.sum()):>8}{100*np.mean(e_fw[msk]<DEADBAND):>11.1f}%{100*np.mean(e_pl[msk]<DEADBAND):>9.1f}%")
    print("saved out/x2d_plus.png")

if __name__ == "__main__":
    main()
