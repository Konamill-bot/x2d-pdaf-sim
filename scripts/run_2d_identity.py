# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Two realism gaps of AF-T: a 2-D zone grid, and subject identity.

Scene: 64 x 512 PDAF views on a 4 x 8 grid of AF cells (16 x 64 px each). A small subject
(1 x 2 cells) wanders in 2-D and in depth in front of a far background. Twice per clip a
distractor of the same size crosses the subject's row; at the crossing it is at exactly the
subject's depth and covers it, then the two separate in depth. A depth-only tracker cannot
tell them apart at the crossing; a visual identity cue can.

Configurations (the brain is DualGated in every case):
  AF-C, whole area        : median of all cells (no tracking)
  1-D bands, ZoneTracker  : the existing chain on 8 full-width bands (a small subject is
                            mixed with the background in every band)
  2-D cells, depth only   : cells near the predicted subject depth AND near its predicted
                            2-D position (alpha-beta centroid); nearest cluster wins
  2-D + visual ROI        : a simulated detector + multi-object tracker gives the subject's
                            cells: 30 Hz, 3 frames late, 10 % misses, occasional one-cell
                            jitter, and a 30 % chance per crossing of an identity switch onto
                            the distractor. Depth verifies the ROI and vetoes it when the
                            depth-only tracker still sees the subject near its prediction.
  2-D + ideal ROI         : the exact visible subject cells (upper bound)

Parameters were set a priori (no tuning). Scored against the subject.
Run:  python scripts/run_2d_identity.py  ->  out/2d_identity.png   (~5 min)
"""
from __future__ import annotations
import importlib.util, os, sys, time
from dataclasses import dataclass, field
from multiprocessing import Pool
import numpy as np
from scipy import ndimage
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_x2d_plus", os.path.join(HERE, "run_x2d_plus.py"))
X = importlib.util.module_from_spec(spec); sys.modules["run_x2d_plus"] = X; spec.loader.exec_module(X)
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity
from pdaf_sim.policy_dual import DualGated
from pdaf_sim.zone_tracker import ZoneTracker, zone_estimates, combine_zones, ZS0, ZP

FPS, NFR = 60, 15 * 60
H, W, NR, NC, CH, CW = 64, 512, 4, 8, 16, 64          # views and the 4 x 8 cell grid
NB, MD_CELL, MD_BAND = 8, 24, 48                       # 1-D bands; disparity search (px)
PPM, LAT, DB = X.PX_PER_MM, X.LATENCY, X.DEADBAND
ROW, COL = np.meshgrid(np.arange(NR), np.arange(NC), indexing="ij")
CFGS = ["AF-C, whole area", "1-D bands, ZoneTracker", "2-D cells, depth only",
        "2-D + visual ROI", "2-D + ideal ROI"]
COLORS = ["#8A8F98", "#E0A030", "#3C8DDE", "#1D9E75", "#7B4FC9"]
INK, INK2, GRID = "#222222", "#555555", "#E6E6E6"


def bars(rng, w=W):
    img = np.zeros((H, w), dtype=np.float32)
    for _ in range(80):
        x = rng.integers(0, w); hw = rng.integers(2, 8)
        img[:, max(0, x - hw):x + hw] += rng.uniform(0.3, 1.0)
    img += rng.normal(0, 0.02, img.shape)
    return np.clip(img / img.max(), 0, 1).astype(np.float32)


def scenario(seed):
    """Per-frame subject / distractor state and the cells each one visibly owns."""
    rng = np.random.default_rng(seed)
    t = np.arange(NFR) / FPS
    pos = np.zeros((NFR, 2)); v = np.zeros(2); p = np.array([rng.uniform(0.5, 2.5), rng.uniform(1, 5)])
    ds = np.zeros(NFR); d = rng.uniform(2.0, 3.5); vd = 0.0
    for k in range(NFR):                                 # smooth 2-D wander + depth wander
        v = 0.98 * v + rng.normal(0, 0.0012, 2); p = p + v
        for i, hi in ((0, NR - 1), (1, NC - 2)):
            if p[i] < 0 or p[i] > hi: p[i] = np.clip(p[i], 0, hi); v[i] = -v[i]
        pos[k] = p
        vd = float(np.clip(0.97 * vd + rng.normal(0, 0.0012), -0.02, 0.02)); d += vd
        if d < 1.5 or d > 4.0: d = float(np.clip(d, 1.5, 4.0)); vd = -vd
        ds[k] = d
    dd = np.full(NFR, np.nan); dpos = np.full((NFR, 2), np.nan); cross = []
    for tc in (rng.uniform(4, 6), rng.uniform(10, 12)):  # two crossings
        kc = int(tc * FPS); side = rng.choice([-1, 1]); slope = rng.choice([-1, 1]) * rng.uniform(0.4, 1.2)
        speed = rng.uniform(3.0, 5.0) / FPS              # cells per frame
        if kc >= NFR:
            continue
        for k in range(NFR):
            c = pos[kc, 1] + side * speed * (kc - k)     # passes the subject's column at kc
            if -2 < c < NC + 1:
                dpos[k] = (round(pos[kc, 0]), c)
                dd[k] = max(0.8, ds[kc] + slope * (k - kc) / FPS)
        cross.append(kc)
    own_s = np.zeros((NFR, NR, NC), bool); own_d = np.zeros((NFR, NR, NC), bool)
    for k in range(NFR):
        r, c = int(round(pos[k, 0])), int(round(pos[k, 1]))
        sm = (ROW == r) & ((COL == c) | (COL == c + 1))
        dm = np.zeros((NR, NC), bool)
        if not np.isnan(dd[k]):
            r2, c2 = int(dpos[k, 0]), int(round(dpos[k, 1]))
            dm = (ROW == r2) & ((COL == c2) | (COL == c2 + 1))
        dist_in_front = not np.isnan(dd[k]) and dd[k] <= ds[k]
        own_s[k] = sm & ~(dm & dist_in_front)
        own_d[k] = dm & ~(sm & ~dist_in_front)
    return dict(t=t, pos=pos, ds=ds, dd=dd, own_s=own_s, own_d=own_d, cross=cross)


def make_roi(sc, seed, p_miss=0.10, p_jit=0.10, p_switch=0.30, rate=2):
    """The simulated detector + MOT output per frame (before its 3-frame delay)."""
    rng = np.random.default_rng(seed + 1000)
    roi = [None] * NFR; switched_until = -1; decided = set()
    for k in range(NFR):
        if k % rate:
            roi[k] = roi[k - 1]; continue
        if rng.random() < p_miss:
            roi[k] = None; continue
        overlap = sc["own_d"][k].any() and not sc["own_s"][k].any()   # distractor covers the subject
        for kc in sc["cross"]:
            if overlap and abs(k - kc) < 60 and kc not in decided:
                decided.add(kc)
                if rng.random() < p_switch:              # identity switch onto the distractor
                    gone = [j for j in range(k, NFR) if not sc["own_d"][j].any()]
                    switched_until = (gone[0] if gone else NFR) + 30
        m = sc["own_d"][k] if (k < switched_until and sc["own_d"][k].any()) else sc["own_s"][k]
        if not m.any():
            roi[k] = None; continue
        if rng.random() < p_jit:
            m = np.roll(m, rng.choice([-1, 1]), axis=1)
        roi[k] = m.copy()
    return roi


@dataclass
class Tracker2D:
    """Depth + 2-D position association on the cell grid, optionally fed a visual ROI."""
    gate: float = 3.5; r_gate: float = 1.5; alpha: float = 0.5; beta: float = 0.1
    max_hold: int = 90; c_floor: float = 0.05; conflict_accept: int = 6
    pos: np.ndarray = field(default_factory=lambda: np.zeros(2)); vel: np.ndarray = field(default_factory=lambda: np.zeros(2))
    hold: int = 0; conflict: int = 0

    def _depth_only(self, ok, ppos):
        dist = np.hypot(ROW - ppos[0], COL - ppos[1])
        cand = ok & (dist <= self.r_gate + 0.05 * self.hold)
        if not cand.any():
            return None
        lab, n = ndimage.label(cand)
        best = min(range(1, n + 1), key=lambda i: np.hypot(ROW[lab == i].mean() - ppos[0], COL[lab == i].mean() - ppos[1]))
        return lab == best

    def _update_pos(self, sel, ppos):
        meas = np.array([ROW[sel].mean(), COL[sel].mean()]); res = meas - ppos
        self.pos = ppos + self.alpha * res; self.vel = self.vel + self.beta * res; self.hold = 0

    def select(self, z, conf, pred, roi=None, use_roi=False):
        valid = conf >= self.c_floor
        ppos = self.pos + self.vel
        if pred is None:                                 # acquisition: the user's tap, or the ROI
            if use_roi and roi is not None and roi.any():
                sel = roi & valid
            else:
                sel = valid & (np.hypot(ROW - ppos[0], COL - ppos[1]) <= 0.75)
            if sel.any(): self._update_pos(sel, ppos)
            return sel if sel.any() else None
        x, var = pred
        sig = ZS0 * np.maximum(conf, self.c_floor) ** -ZP
        ok = valid & ((z - x) ** 2 <= self.gate ** 2 * (var + sig * sig))
        sel = None
        if use_roi and roi is not None and roi.any():
            both = roi & ok
            if both.any():
                sel = both; self.conflict = 0
            else:
                sel = self._depth_only(ok, ppos)         # ROI disagrees with depth: is the subject still here?
                if sel is None:
                    self.conflict += 1
                    if self.conflict >= self.conflict_accept:
                        sel = roi & valid                # the detector insists: the subject moved
                        self.conflict = 0
        else:
            sel = self._depth_only(ok, ppos)
        if sel is not None and sel.any():
            self._update_pos(sel, ppos); return sel
        self.pos = ppos; self.vel = 0.95 * self.vel; self.hold += 1
        return None if self.hold < self.max_hold else valid


def run(seed, cfg):
    sc = scenario(seed); roi = make_roi(sc, seed) if cfg == 3 else None
    rng = np.random.default_rng(seed + 1); nrng = np.random.default_rng(seed + 8)
    tex_b, tex_s, tex_d = bars(rng), bars(rng), bars(rng)
    bg_mm = X.focus_mm(12.0)
    brain = DualGated(); zt = ZoneTracker(); t2 = Tracker2D(); lb = X.LatencyBuffer(LAT)
    s0m = sc["own_s"][0]                                  # the user's tap: the subject's centre
    t2.pos = np.array([ROW[s0m].mean(), COL[s0m].mean()])
    lens = X.focus_mm(sc["ds"][0]); last = lens; motor = X.make_motor(lens, X.SPEED_TARGET)
    track = np.zeros(NFR)
    for k in range(NFR):
        s_mm = X.focus_mm(sc["ds"][k]); d_mm = X.focus_mm(sc["dd"][k]) if not np.isnan(sc["dd"][k]) else None
        Lb, Rb = render_lr(tex_b, lens - bg_mm, X.F_MM, X.FNUM, X.DIST, X.PIX, noise_sigma=0.0)
        Ls, Rs = render_lr(tex_s, lens - s_mm, X.F_MM, X.FNUM, X.DIST, X.PIX, noise_sigma=0.0)
        own_s = np.kron(sc["own_s"][k], np.ones((CH, CW), bool))
        L = np.where(own_s, Ls, Lb); R = np.where(own_s, Rs, Rb)
        if d_mm is not None and sc["own_d"][k].any():
            Ld, Rd = render_lr(tex_d, lens - d_mm, X.F_MM, X.FNUM, X.DIST, X.PIX, noise_sigma=0.0)
            own_d = np.kron(sc["own_d"][k], np.ones((CH, CW), bool))
            L = np.where(own_d, Ld, L); R = np.where(own_d, Rd, R)
        L = (L + nrng.normal(0, 0.006, L.shape)).astype(np.float32)
        R = (R + nrng.normal(0, 0.006, R.shape)).astype(np.float32)
        if cfg == 1:
            meas = zone_estimates(L, R, NB, MD_BAND)
        else:
            dc = np.zeros((NR, NC)); cc = np.zeros((NR, NC))
            for r in range(NR):
                for c in range(NC):
                    dc[r, c], cc[r, c] = estimate_disparity(L[r*CH:(r+1)*CH, c*CW:(c+1)*CW],
                                                            R[r*CH:(r+1)*CH, c*CW:(c+1)*CW], max_disp_px=MD_CELL)
            meas = (dc, cc)
        arr = lb.push_pop((meas, lens, k))
        if arr is None:                                  # pipeline warm-up: hold the lens
            last = brain.coast() if brain.predict() is not None else lens
        else:
            (dpx, cz), lens_m, j = arr
            if cfg == 0:
                d, c = combine_zones(dpx.ravel(), cz.ravel()); last = brain.step(d / PPM, c, 0.0, lens_m)
            elif cfg == 1:
                sel = zt.select(lens_m - np.asarray(dpx) / PPM, np.asarray(cz), brain.predict())
                if sel is None: last = brain.coast()
                else:
                    use = sel if np.any(sel) else np.ones(NB, bool)
                    d, c = combine_zones(np.asarray(dpx)[use], np.asarray(cz)[use]); last = brain.step(d / PPM, c, 0.0, lens_m)
            else:
                if cfg == 4:
                    sel = sc["own_s"][j] if sc["own_s"][j].any() else None
                else:
                    r_avail = roi[j - 3] if (cfg == 3 and j >= 3) else None
                    sel = t2.select(lens_m - dpx / PPM, cz, brain.predict(), roi=r_avail, use_roi=cfg == 3)
                if sel is None or not np.any(sel):
                    last = brain.coast()
                else:
                    d, c = combine_zones(dpx[sel], cz[sel]); last = brain.step(d / PPM, c, 0.0, lens_m)
        lens = motor.command(last); track[k] = lens
    return track


def _job(a):
    s, cfg = a
    return s, cfg, run(s, cfg)


def main():
    seeds = list(range(20))
    os.makedirs("out", exist_ok=True)
    t0 = time.time()
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, [(s, c) for s in seeds for c in range(len(CFGS))])
    tr = {(s, c): t for s, c, t in out}
    print(f"sim compute: {time.time()-t0:.0f}s ({len(seeds)} seeds x {len(CFGS)} configs)\n")
    cols = ["overall", "crossing +-0.5 s", "after crossing", "on distractor (after)"]
    stats = {c: [] for c in range(len(CFGS))}
    for s in seeds:
        sc = scenario(s); ts = np.array([X.focus_mm(d) for d in sc["ds"]])
        td = np.array([X.focus_mm(d) if not np.isnan(d) else np.nan for d in sc["dd"]])
        k = np.arange(NFR)
        near = np.zeros(NFR, bool); after = np.zeros(NFR, bool)
        for kc in sc["cross"]:
            near |= np.abs(k - kc) <= 30; after |= (k > kc + 30) & (k <= kc + 150)
        apart = after & (np.abs(td - ts) > 0.2)
        for c in range(len(CFGS)):
            e = np.abs(tr[(s, c)] - ts) < DB
            ond = np.abs(tr[(s, c)] - td) < DB
            stats[c].append([100 * e.mean(), 100 * e[near].mean(), 100 * e[after].mean(),
                             100 * ond[apart].mean() if apart.any() else np.nan])
    print(f"in-focus on the SUBJECT %, mean ± sem over {len(seeds)} seeds")
    print(f"   {'':<26}" + "".join(f"{c:>24}" for c in cols))
    for c, name in enumerate(CFGS):
        a = np.array(stats[c]); m = np.nanmean(a, 0); se = np.nanstd(a, 0) / np.sqrt(len(seeds))
        print(f"   {name:<26}" + "".join(f"{x:>17.1f} ± {y:<4.1f}" for x, y in zip(m, se)))

    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.titlecolor": INK, "font.size": 10})
    fig, ax = plt.subplots(2, 1, figsize=(15, 10.5), gridspec_kw={"height_ratios": [1.1, 1]})
    for a in ax:
        a.grid(axis="y", color=GRID, lw=1); a.set_axisbelow(True)
        for sp in ("top", "right"): a.spines[sp].set_visible(False)
    s0 = seeds[0]; sc = scenario(s0); t = sc["t"]
    for kc in sc["cross"]:
        ax[0].axvspan((kc - 30) / FPS, (kc + 30) / FPS, color="#D9DCE1", lw=0)
    ax[0].plot(t, [X.focus_mm(d) for d in sc["ds"]], color=INK, lw=2.2, label="subject")
    ax[0].plot(t, [X.focus_mm(d) if not np.isnan(d) else np.nan for d in sc["dd"]], color=INK2, lw=2, ls=":",
               label="distractor (grey band: crossing)")
    for c in (1, 2, 3, 4):
        ax[0].plot(t, tr[(s0, c)], color=COLORS[c], lw=1.6, label=f"lens: {CFGS[c]}")
    ax[0].set_ylabel("lens position (mm)"); ax[0].set_xlabel("time (s)")
    ax[0].set_title(f"Seed {s0}: a small subject, and a distractor crossing at the subject's depth")
    ax[0].legend(fontsize=8.5, frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    xg = np.arange(len(cols)); w = 0.16
    for c in range(len(CFGS)):
        a = np.array(stats[c]); m = np.nanmean(a, 0); se = np.nanstd(a, 0) / np.sqrt(len(seeds))
        ax[1].bar(xg + (c - 2) * w, m, w * 0.92, yerr=se, color=COLORS[c], label=CFGS[c], capsize=2,
                  error_kw={"lw": 1, "ecolor": INK2})
    ax[1].set_xticks(xg); ax[1].set_xticklabels(["in focus on the subject:\noverall", "around a crossing (±0.5 s)",
                                                 "0.5-2.5 s after a crossing", "lens on the DISTRACTOR\nafter a crossing (lower = better)"], fontsize=9)
    ax[1].set_ylim(0, 118); ax[1].set_ylabel("% of frames (mean ± sem)")
    ax[1].set_title(f"{len(seeds)} seeds; parameters set a priori")
    ax[1].legend(fontsize=8.5, frameon=False, ncol=5, loc="upper left")
    fig.suptitle("Small subject on a 2-D AF grid, with a same-depth distractor: depth, 2-D and identity",
                 fontsize=13, color=INK)
    fig.tight_layout(rect=[0, 0, 1, 0.97]); fig.savefig("out/2d_identity.png", dpi=120)
    print("\nsaved out/2d_identity.png")


if __name__ == "__main__":
    main()
