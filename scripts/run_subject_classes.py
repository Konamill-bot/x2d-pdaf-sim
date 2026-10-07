# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Subject classes for AF-T: a person, an animal, a bird.

run_2d_identity.py used one generic subject and a class-agnostic simulated detector. A real
camera's detector is trained on classes, and the classes differ in three ways that matter
to AF:
  * size against the AF cell : a person spans several cells; a small bird is smaller than
                               one cell, so every cell it touches also sees what is behind it
  * motion                   : a person drifts; an animal accelerates and turns; a small
                               bird darts
  * detector quality         : people are the most mature class; animals vary more; small
                               birds are the hardest (detectors are weakest on small objects,
                               and two birds of one species are nearly impossible to tell
                               apart, so an identity switch at a crossing is close to a coin flip)
A fourth case, the bird against open sky, takes the background texture away.

Everything else is identical: the 4 x 8 cell grid, the DualGated brain, Tracker2D, the
depth band (1.5-4 m), a same-class, same-size distractor crossing twice at the subject's
depth, and (except open sky) a textured background at 12 m. Positions are continuous, so
an object straddles cells and the views are composited per pixel. The AF uses the cells
holding at least a quarter of the subject (or of the detector's box).

Configurations, per class:
  depth only   : no detector, as for a subject outside the detector's classes
  class ROI    : the class's simulated detector + MOT (30 Hz, 3 frames late), fused as in
                 run_2d_identity.py: Tracker2D, where depth leads and vetoes the ROI
  class ROI, ROI leads : the same detector's cells, measured directly; a measurement that
                 disagrees with the track's depth is vetoed for up to 6 frames
  ideal ROI    : the exact visible subject, same cell rule (upper bound for fixed cells)
  ideal ROI, fitted window : one PDAF window cut to the visible subject's box instead of
                 fixed cells (upper bound for subject-fitted PDAF)
  class box, fitted window : one PDAF window cut to the class detector's box (3 frames old),
                 with the same ROI-leads veto

The class parameters (PROFILES) are ASSUMPTIONS set a priori from the points above, not
measurements of any camera or detector: read the ordering and the reasons, not the decimals.
Run:  python scripts/run_subject_classes.py           ->  out/subject_classes.png   (~12 min)
      python scripts/run_subject_classes.py --quick   (1 seed, no figure)
"""
from __future__ import annotations
import importlib.util, os, sys, time
from multiprocessing import Pool
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_2d_identity", os.path.join(HERE, "run_2d_identity.py"))
I = importlib.util.module_from_spec(spec); sys.modules["run_2d_identity"] = I; spec.loader.exec_module(I)
X = I.X
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity
from pdaf_sim.policy_dual import DualGated
from pdaf_sim.zone_tracker import combine_zones, ZS0, ZP

FPS, NFR, H, W, NR, NC, CH, CW = I.FPS, I.NFR, I.H, I.W, I.NR, I.NC, I.CH, I.CW
MD_CELL, PPM, LAT, DB, ROW, COL = I.MD_CELL, I.PPM, I.LAT, I.DB, I.ROW, I.COL

# Per class: size (px; a cell is 16 x 64 px), 2-D motion (cells/frame: noise, damping), depth
# motion (m/frame: noise, damping, cap) and sudden darts (probability per frame), the
# distractor's crossing speed (cells/s) and depth slope (m/s), and the detector's miss,
# jitter and identity-switch probabilities. ASSUMPTIONS, set a priori.
PROFILES = {
    "person": dict(h=32, w=128, n2=0.0012, a2=0.98, nd=0.0012, ad=0.97, vmax=0.02, dart=0.0,
                   xs=(3, 5), slope=(0.4, 1.2), miss=0.05, jit=0.05, switch=0.20, sky=False),
    "animal": dict(h=24, w=96, n2=0.0025, a2=0.96, nd=0.0025, ad=0.94, vmax=0.05, dart=0.0,
                   xs=(4, 7), slope=(0.8, 2.0), miss=0.15, jit=0.15, switch=0.35, sky=False),
    "bird":   dict(h=10, w=32, n2=0.0060, a2=0.92, nd=0.0040, ad=0.90, vmax=0.08, dart=0.01,
                   xs=(5, 9), slope=(1.0, 3.0), miss=0.30, jit=0.25, switch=0.50, sky=False),
}
PROFILES["bird, open sky"] = dict(PROFILES["bird"], sky=True)
CLASSES = list(PROFILES)
LABELS = ["person\n(2 × 2 cells)", "animal\n(1.5 × 1.5 cells)", "bird, busy background\n(≈ ½ × ½ cell)",
          "bird, open sky\n(≈ ½ × ½ cell)"]
CFGS = ["depth only (no detector)", "class detector → fixed cells, depth leads (Tracker2D)",
        "ideal ROI → fixed cells", "ideal ROI → PDAF window fitted to the subject",
        "class detector → fitted PDAF window, ROI leads", "class detector → fixed cells, ROI leads"]
COLORS = ["#3C8DDE", "#C27C0E", "#7B4FC9", "#7B4FC9", "#1D9E75", "#1D9E75"]
FITTED = [False, False, False, True, True, False]                   # fitted windows are hatched
ORDER = [0, 1, 5, 4, 2, 3]                                          # bar order in the figure
INK, INK2, GRID = I.INK, I.INK2, I.GRID


def scenario(seed, P):
    """Per-frame top-left corners (cells, continuous) and depths of the subject and of a
    same-class distractor that crosses it twice at its depth."""
    rng = np.random.default_rng(seed)
    hc, wc = P["h"] / CH, P["w"] / CW
    lim = np.array([NR - hc, NC - wc])
    pos = np.zeros((NFR, 2)); v = np.zeros(2); p = rng.uniform(0, 1, 2) * lim
    ds = np.zeros(NFR); d = rng.uniform(2.0, 3.5); vd = 0.0
    for k in range(NFR):
        v = P["a2"] * v + rng.normal(0, P["n2"], 2)
        vd = P["ad"] * vd + rng.normal(0, P["nd"])
        if P["dart"] and rng.random() < P["dart"]:      # a sudden dart
            v = v + rng.normal(0, 0.05, 2); vd += rng.choice([-1, 1]) * 0.6 * P["vmax"]
        p = p + v
        for i in (0, 1):
            if p[i] < 0 or p[i] > lim[i]: p[i] = np.clip(p[i], 0, lim[i]); v[i] = -v[i]
        pos[k] = p
        vd = float(np.clip(vd, -P["vmax"], P["vmax"])); d += vd
        if d < 1.5 or d > 4.0: d = float(np.clip(d, 1.5, 4.0)); vd = -vd
        ds[k] = d
    dd = np.full(NFR, np.nan); dpos = np.full((NFR, 2), np.nan); cross = []
    for tc in (rng.uniform(4, 6), rng.uniform(10, 12)):
        kc = int(tc * FPS); side = rng.choice([-1, 1]); slope = rng.choice([-1, 1]) * rng.uniform(*P["slope"])
        speed = rng.uniform(*P["xs"]) / FPS
        if kc >= NFR:
            continue
        for k in range(NFR):
            c = pos[kc, 1] + side * speed * (kc - k)    # passes the subject's column at kc
            if -wc - 1 < c < NC + 1:
                dpos[k] = (pos[kc, 0], c)
                dd[k] = min(10.0, max(0.8, ds[kc] + slope * (k - kc) / FPS))
        cross.append(kc)
    return dict(t=np.arange(NFR) / FPS, pos=pos, ds=ds, dpos=dpos, dd=dd, cross=cross)


def rect(p, P):
    """Pixel mask of a P-sized object whose top-left corner is at p (cells)."""
    m = np.zeros((H, W), bool)
    y, x = int(round(p[0] * CH)), int(round(p[1] * CW))
    m[max(0, y):max(0, y + P["h"]), max(0, x):max(0, x + P["w"])] = True
    return m


def masks(sc, k, P):
    """Visible pixels of the subject and of the distractor at frame k (the nearer one wins)."""
    ms = rect(sc["pos"][k], P)
    if np.isnan(sc["dd"][k]):
        return ms, np.zeros_like(ms)
    md = rect(sc["dpos"][k], P)
    if sc["dd"][k] <= sc["ds"][k]: ms = ms & ~md
    else: md = md & ~ms
    return ms, md


def cells(m):
    """The AF cells for an object's pixels: cells holding >= 1/4 of them, else the one holding most."""
    n = m.reshape(NR, CH, NC, CW).sum(axis=(1, 3)); tot = n.sum()
    if tot == 0:
        return None
    sel = n >= 0.25 * tot
    return sel if sel.any() else n == n.max()


def make_roi(sc, seed, P, rate=2):
    """The class's simulated detector + MOT: per frame, the AF cells under its box and the box
    itself (before the 3-frame delay)."""
    rng = np.random.default_rng(seed + 1000)
    roi = [None] * NFR; boxes = [None] * NFR; switched_until = -1; decided = set(); area = P["h"] * P["w"]
    for k in range(NFR):
        if k % rate:
            roi[k], boxes[k] = roi[k - 1], boxes[k - 1]; continue
        if rng.random() < P["miss"]:
            continue
        ms, md = masks(sc, k, P)
        hidden = md.any() and ms.sum() < 0.5 * area     # the distractor covers the subject
        for kc in sc["cross"]:
            if hidden and abs(k - kc) < 60 and kc not in decided:
                decided.add(kc)
                if rng.random() < P["switch"]:          # identity switch onto the distractor
                    gone = next((j for j in range(k, NFR) if not masks(sc, j, P)[1].any()), NFR)
                    switched_until = gone + 30
        m = md if (k < switched_until and md.any()) else ms
        if not m.any():
            continue
        ys, xs = np.nonzero(m)                          # the box: the object's visible extent
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        if rng.random() < P["jit"]:
            dx = int(rng.choice([-1, 1]) * P["w"] // 2); x0, x1 = x0 + dx, x1 + dx
        x0, x1 = max(0, x0), min(W, x1)
        if x1 - x0 < 8:                                 # jittered off the frame
            continue
        box = np.zeros((H, W), bool); box[y0:y1, x0:x1] = True
        roi[k], boxes[k] = cells(box), (slice(y0, y1), slice(x0, x1))
    return roi, boxes


def run(seed, cls, cfg):
    P = PROFILES[cls]; sc = scenario(seed, P)
    roi, boxes = make_roi(sc, seed, P) if cfg in (1, 4, 5) else (None, None)
    rng = np.random.default_rng(seed + 1); nrng = np.random.default_rng(seed + 8)
    tex_b, tex_s, tex_d = I.bars(rng), I.bars(rng), I.bars(rng)
    if P["sky"]:
        tex_b = np.full_like(tex_b, 0.5)                # open sky: nothing behind the bird to lock on
    bg_mm = X.focus_mm(12.0)
    brain = DualGated(); t2 = I.Tracker2D(); lb = X.LatencyBuffer(LAT)
    c0 = cells(masks(sc, 0, P)[0])                      # the user's tap: the subject's centre
    t2.pos = np.array([ROW[c0].mean(), COL[c0].mean()])
    lens = X.focus_mm(sc["ds"][0]); last = lens; motor = X.make_motor(lens, X.SPEED_TARGET)
    track = np.zeros(NFR); own = [None] * NFR; nconf = 0
    for k in range(NFR):
        ms, md = masks(sc, k, P); own[k] = cells(ms)
        Lb, Rb = render_lr(tex_b, lens - bg_mm, X.F_MM, X.FNUM, X.DIST, X.PIX, noise_sigma=0.0)
        Ls, Rs = render_lr(tex_s, lens - X.focus_mm(sc["ds"][k]), X.F_MM, X.FNUM, X.DIST, X.PIX, noise_sigma=0.0)
        L = np.where(ms, Ls, Lb); R = np.where(ms, Rs, Rb)
        if md.any():
            Ld, Rd = render_lr(tex_d, lens - X.focus_mm(sc["dd"][k]), X.F_MM, X.FNUM, X.DIST, X.PIX, noise_sigma=0.0)
            L = np.where(md, Ld, L); R = np.where(md, Rd, R)
        L = (L + nrng.normal(0, 0.006, L.shape)).astype(np.float32)
        R = (R + nrng.normal(0, 0.006, R.shape)).astype(np.float32)
        if cfg in (3, 4):                               # one PDAF window cut to a box
            win = None
            if cfg == 3 and ms.any():                   # the subject's exact visible box
                ys, xs = np.nonzero(ms); win = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
            elif cfg == 4 and k >= 3:                   # the detector's latest box (3 frames old)
                win = boxes[k - 3]
            meas = None if win is None else estimate_disparity(L[win], R[win], max_disp_px=MD_CELL)
        else:
            dc = np.zeros((NR, NC)); cc = np.zeros((NR, NC))
            for r in range(NR):
                for c in range(NC):
                    dc[r, c], cc[r, c] = estimate_disparity(L[r*CH:(r+1)*CH, c*CW:(c+1)*CW],
                                                            R[r*CH:(r+1)*CH, c*CW:(c+1)*CW], max_disp_px=MD_CELL)
            meas = (dc, cc)
        arr = lb.push_pop((meas, lens, k))
        if arr is None:                                 # pipeline warm-up: hold the lens
            last = brain.coast() if brain.predict() is not None else lens
        else:
            meas, lens_m, j = arr
            if cfg in (3, 4, 5):                        # one measurement per frame
                if cfg == 5:                            # the detector's cells, combined directly
                    sel = roi[j - 3] if j >= 3 else None
                    meas = combine_zones(meas[0][sel], meas[1][sel]) if sel is not None and sel.any() else None
                pred = brain.predict()
                if cfg != 3 and meas is not None and pred is not None:
                    z = lens_m - meas[0] / PPM; sig = ZS0 * max(meas[1], 0.05) ** -ZP
                    if (z - pred[0]) ** 2 > 3.5 ** 2 * (pred[1] + sig * sig):   # the box disagrees with the track
                        nconf += 1
                        if nconf < 6: meas = None       # veto, unless the detector insists
                        else: nconf = 0
                    else:
                        nconf = 0
                if meas is None:
                    last = brain.coast() if pred is not None else lens   # no track yet: hold
                else:
                    last = brain.step(meas[0] / PPM, meas[1], 0.0, lens_m)
                lens = motor.command(last); track[k] = lens
                continue
            dpx, cz = meas
            if cfg == 2:
                sel = own[j]
            else:
                r_avail = roi[j - 3] if (cfg == 1 and j >= 3) else None
                sel = t2.select(lens_m - dpx / PPM, cz, brain.predict(), roi=r_avail, use_roi=cfg == 1)
            if sel is None or not np.any(sel):
                last = brain.coast()
            else:
                d, c = combine_zones(dpx[sel], cz[sel]); last = brain.step(d / PPM, c, 0.0, lens_m)
        lens = motor.command(last); track[k] = lens
    return track


def score(seed, cls, track):
    """In focus on the subject (whole clip; 0.5-2.5 s after each crossing) and on the distractor."""
    sc = scenario(seed, PROFILES[cls])
    ts = np.array([X.focus_mm(d) for d in sc["ds"]])
    td = np.array([X.focus_mm(d) if not np.isnan(d) else np.nan for d in sc["dd"]])
    k = np.arange(NFR); after = np.zeros(NFR, bool)
    for kc in sc["cross"]:
        after |= (k > kc + 30) & (k <= kc + 150)
    apart = after & (np.abs(td - ts) > 0.2)
    e = np.abs(track - ts) < DB; ond = np.abs(track - td) < DB
    return [100 * e.mean(), 100 * e[after].mean(), 100 * ond[apart].mean() if apart.any() else np.nan]


def figure(M, n):
    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.titlecolor": INK, "font.size": 10})
    fig, ax = plt.subplots(1, 2, figsize=(15, 6.4), gridspec_kw={"width_ratios": [1.35, 1]})
    xg = np.arange(len(CLASSES)); w = 0.135
    for a, (col, title) in zip(ax, [(0, "in focus on the subject, whole clip (%)"),
                                    (2, "lens on the DISTRACTOR after a crossing (%, lower = better)")]):
        a.grid(axis="y", color=GRID, lw=1); a.set_axisbelow(True)
        for sp in ("top", "right"): a.spines[sp].set_visible(False)
        for i, c in enumerate(ORDER):
            m = [M[(cls, c)][0][col] for cls in CLASSES]; se = [M[(cls, c)][1][col] for cls in CLASSES]
            b = a.bar(xg + (i - 2.5) * w, m, w - 0.016, yerr=se, color=COLORS[c], label=CFGS[c], capsize=2,
                      hatch="////" if FITTED[c] else None, edgecolor="white", linewidth=0,
                      error_kw={"lw": 1, "ecolor": INK2})
            if c in (4, 5):                             # direct labels on the ROI-led detector series only
                a.bar_label(b, labels=[f"{v:.0f}" for v in m], padding=4, fontsize=9, color=INK)
        a.set_xticks(xg); a.set_xticklabels(LABELS, fontsize=9); a.set_title(title, fontsize=10.5)
        a.set_ylim(0, 108)
    h, l = ax[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, frameon=False, fontsize=9.5)
    fig.suptitle(f"AF-T by subject class: size against the AF cell, motion and detector quality "
                 f"({n} seeds; class parameters assumed a priori)", fontsize=12.5, color=INK)
    fig.tight_layout(rect=[0, 0.11, 1, 0.95]); fig.savefig("out/subject_classes.png", dpi=120)


def _job(a):
    s, cls, cfg = a
    return s, cls, cfg, run(s, cls, cfg)


def main():
    quick = "--quick" in sys.argv
    seeds = [0] if quick else list(range(20))
    t0 = time.time()
    jobs = [(s, cls, c) for cls in CLASSES for s in seeds for c in range(len(CFGS))]
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, jobs, chunksize=1)
    print(f"sim compute: {time.time()-t0:.0f}s ({len(jobs)} runs)\n")
    st = {}
    for s, cls, c, tr in out:
        st.setdefault((cls, c), []).append(score(s, cls, tr))
    cols = ["in focus, whole clip", "0.5-2.5 s after crossing", "on distractor (after)"]
    print(f"AF-T in focus on the SUBJECT %, mean ± sem over {len(seeds)} seed(s)")
    print(f"   {'':<64}" + "".join(f"{c:>28}" for c in cols))
    M = {}
    for cls in CLASSES:
        for c, name in enumerate(CFGS):
            a = np.array(st[(cls, c)]); m = np.nanmean(a, 0); se = np.nanstd(a, 0) / np.sqrt(len(seeds))
            M[(cls, c)] = (m, se)
            print(f"   {cls + ': ' + name:<64}" + "".join(f"{x:>21.1f} ± {y:<4.1f}" for x, y in zip(m, se)))
    if not quick:
        os.makedirs("out", exist_ok=True); figure(M, len(seeds)); print("\nsaved out/subject_classes.png")


if __name__ == "__main__":
    main()
