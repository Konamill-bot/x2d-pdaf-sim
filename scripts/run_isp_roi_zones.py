# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""ISP model B: one ROI per frame, split into zones.

run_isp_window.py modelled the ISP as measuring one window cut to the detector's box. Many ISPs
work differently. PD statistics come on a grid of small blocks, and the AF reads ONE ROI per
frame, split into a few zones (here up to 3 x 3) that each return their own (disparity,
confidence). The ROI spans a power-of-two number of blocks; every zone must be wide enough for
the disparity search and tall enough for enough PD lines. The AF uses the zone the detector's
box fills most (pdaf_sim/roi.py: ZonedRoi; scripts/run_subject_classes.py: best_zone).

Every class is run three ways: window = the detector's box; one window under ISP rules
(run_isp_window.py's baseline); one ROI split into 3 x 3 zones. Then, for the bird against a
busy background, one assumption is changed at a time.

Generic placeholder values, not any particular ISP's. Put your own in BASE and VARIANTS
locally; numbers taken from a real ISP do not belong in a public repository.
Run:  python scripts/run_isp_roi_zones.py  ->  out/isp_roi_zones.png   (~4 min on 4 cores)
"""
from __future__ import annotations
import importlib.util, os, sys, time
from multiprocessing import Pool
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_subject_classes", os.path.join(HERE, "run_subject_classes.py"))
S = importlib.util.module_from_spec(spec); sys.modules["run_subject_classes"] = S; spec.loader.exec_module(S)
from pdaf_sim.roi import IspWindow, ZonedRoi

BASE = dict(zones=ZonedRoi(), lat=1, quant=False)
GREEN, AMBER, BLUE, GREY = "#1D9E75", "#C27C0E", "#3C8DDE", "#8A8F98"   # validated with dataviz's validator
CONFIGS = [("window = the detector's box (no ISP rules)", None, GREEN),
           ("one window, ISP rules (run_isp_window.py)", dict(win=IspWindow(), lat=1, quant=True), AMBER),
           ("one ROI split into 3 × 3 zones", BASE, BLUE)]
VARIANTS = [
    ("baseline: 3 × 3 zones, 4 × 8 px blocks", BASE),
    ("1 zone (the whole ROI)", dict(BASE, zones=ZonedRoi(1, 1))),
    ("2 × 2 zones", dict(BASE, zones=ZonedRoi(2, 2))),
    ("4 × 4 zones", dict(BASE, zones=ZonedRoi(4, 4))),
    ("zones at least 48 px wide", dict(BASE, zones=ZonedRoi(zone_min_w=48))),
    ("ROI sizes not limited to powers of two", dict(BASE, zones=ZonedRoi(pow2=False))),
    ("8 × 16 px blocks", dict(BASE, zones=ZonedRoi(bh=8, bw=16))),
    ("+2 frames before a new ROI takes effect", dict(BASE, lat=2)),
]
BIRD = "bird"
INK, INK2, GRID = S.INK, S.INK2, S.GRID


def _job(a):
    s, cls, kind, i = a
    isp = CONFIGS[i][1] if kind == "c" else VARIANTS[i][1]
    return s, cls, kind, i, S.score(s, cls, S.run(s, cls, 4, isp=isp))[0]


def figure(res, n):
    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.titlecolor": INK, "font.size": 10})
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.8), gridspec_kw={"width_ratios": [1.1, 1.1]})
    for a, axis in zip(ax, ("y", "x")):
        a.grid(axis=axis, color=GRID, lw=1); a.set_axisbelow(True)
        for sp in ("top", "right"): a.spines[sp].set_visible(False)
    xg = np.arange(len(S.CLASSES)); w = 0.26
    for i, (lab, _, col) in enumerate(CONFIGS):
        m = [np.mean(res[(c, "c", i)]) for c in S.CLASSES]; se = [np.std(res[(c, "c", i)]) / np.sqrt(n) for c in S.CLASSES]
        b = ax[0].bar(xg + (i - 1) * w, m, w - 0.025, yerr=se, color=col, label=lab, capsize=2,
                      error_kw={"lw": 1, "ecolor": INK2})
        ax[0].bar_label(b, labels=[f"{x:.0f}" for x in m], padding=4, fontsize=8.5, color=INK)
    ax[0].set_xticks(xg); ax[0].set_xticklabels(S.LABELS, fontsize=9); ax[0].set_ylim(0, 112)
    ax[0].set_title("class detector → PDAF measured as …: in focus on the subject (%)", fontsize=10.5)
    ax[0].legend(frameon=False, fontsize=9, ncol=1, loc="upper center", bbox_to_anchor=(0.5, -0.15))
    yv = np.arange(len(VARIANTS))[::-1]
    m = [np.mean(res[(BIRD, "v", v)]) for v in range(len(VARIANTS))]
    se = [np.std(res[(BIRD, "v", v)]) / np.sqrt(n) for v in range(len(VARIANTS))]
    b = ax[1].barh(yv, m, 0.7, xerr=se, color=[BLUE] + [GREY] * (len(VARIANTS) - 1), capsize=2,
                   error_kw={"lw": 1, "ecolor": INK2})
    ax[1].bar_label(b, labels=[f"{x:.0f}" for x in m], padding=8, fontsize=9, color=INK)
    ax[1].set_yticks(yv); ax[1].set_yticklabels([nm for nm, _ in VARIANTS], fontsize=9); ax[1].set_xlim(0, 108)
    ax[1].set_title("bird, busy background, one ROI split into zones:\none assumption changed at a time (in focus, %)",
                    fontsize=10.5)
    fig.suptitle(f"ISP model B: one ROI per frame, split into zones ({n} seeds; generic placeholder values)",
                 fontsize=12.5, color=INK)
    fig.tight_layout(rect=[0, 0.03, 1, 0.94]); fig.savefig("out/isp_roi_zones.png", dpi=120)


def main():
    seeds = list(range(20)); n = len(seeds); t0 = time.time()
    jobs = [(s, cls, "c", i) for cls in S.CLASSES for i in range(len(CONFIGS)) for s in seeds]
    jobs += [(s, BIRD, "v", v) for v in range(1, len(VARIANTS)) for s in seeds]
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, jobs, chunksize=1)
    print(f"sim compute: {time.time()-t0:.0f}s ({len(jobs)} runs)\n")
    res = {}
    for s, cls, kind, i, e in out:
        res.setdefault((cls, kind, i), []).append(e)
    res[(BIRD, "v", 0)] = res[(BIRD, "c", 2)]                    # the baseline is CONFIGS[2]
    print(f"class detector -> PDAF measured as ...: in focus on the subject %, mean ± sem, {n} seeds")
    for cls in S.CLASSES:
        row = [np.array(res[(cls, "c", i)]) for i in range(len(CONFIGS))]
        print(f"   {cls:<16}" + "".join(f"   {lab[:28]:<28} {a.mean():5.1f} ± {a.std() / np.sqrt(n):.1f}"
                                        for (lab, _, _), a in zip(CONFIGS, row))
              + f"   zones - window(ISP) paired {np.mean(row[2] - row[1]):+.1f} ± {np.std(row[2] - row[1]) / np.sqrt(n):.1f}")
    print(f"\n{BIRD}, busy background, one ROI split into zones: one assumption changed at a time (paired vs baseline)")
    base = np.array(res[(BIRD, "v", 0)])
    for v, (name, _) in enumerate(VARIANTS):
        a = np.array(res[(BIRD, "v", v)])
        print(f"   {name:<42} {a.mean():5.1f} ± {a.std() / np.sqrt(n):.1f}   {np.mean(a - base):+5.1f} ± {np.std(a - base) / np.sqrt(n):.1f}")
    os.makedirs("out", exist_ok=True); figure(res, n); print("\nsaved out/isp_roi_zones.png")


if __name__ == "__main__":
    main()
