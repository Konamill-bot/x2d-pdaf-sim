# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Does the ISP matter? The subject-class study measured one PDAF window cut exactly to the
detector's box. An ISP's phase-detection block measures windows under its own rules:
  * a minimum window size (enough PDAF samples, and room for the +-24 px disparity search),
  * window edges on a coarse grid,
  * a newly programmed window takes effect a frame or more later,
  * results come out in fixed point (here 1/16 px disparity, 8-bit confidence).
The values are generic placeholders, not any particular ISP's (pdaf_sim/roi.py: IspWindow);
put your own in BASE and VARIANTS.

Baseline ISP: windows >= 48 x 8 px on an 8 px grid, +1 frame to take effect, quantized output.
Every class is run with the baseline and without ISP rules (the class study's "class detector
-> fitted PDAF window"). Then, for the bird against a busy background (the most sensitive
case), one rule is changed at a time.
Run:  python scripts/run_isp_window.py  ->  out/isp_window.png   (~4 min on 4 cores)
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
from pdaf_sim.roi import IspWindow

BASE = dict(win=IspWindow(align=8, min_w=48, min_h=8), lat=1, quant=True)
VARIANTS = [
    ("baseline ISP rules", BASE),
    ("no ISP rules (window = the box)", None),
    ("new window takes effect at once (+0 frames)", dict(BASE, lat=0)),
    ("+2 frames before a new window takes effect", dict(BASE, lat=2)),
    ("minimum window width 32 px", dict(BASE, win=IspWindow(8, 32, 8))),
    ("minimum window width 64 px", dict(BASE, win=IspWindow(8, 64, 8))),
    ("1 px grid", dict(BASE, win=IspWindow(1, 48, 8))),
    ("16 px grid", dict(BASE, win=IspWindow(16, 48, 8))),
    ("floating-point output", dict(BASE, quant=False)),
]
BIRD = "bird"
INK, INK2, GRID = S.INK, S.INK2, S.GRID
GREEN, AMBER, GREY = "#1D9E75", "#C27C0E", "#8A8F98"   # green/amber validated with dataviz's validator


def _job(a):
    s, cls, v = a
    return s, cls, v, S.score(s, cls, S.run(s, cls, 4, isp=VARIANTS[v][1]))[0]


def figure(res, n):
    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.titlecolor": INK, "font.size": 10})
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.6), gridspec_kw={"width_ratios": [1, 1.2]})
    for a, axis in zip(ax, ("y", "x")):
        a.grid(axis=axis, color=GRID, lw=1); a.set_axisbelow(True)
        for sp in ("top", "right"): a.spines[sp].set_visible(False)
    xg = np.arange(len(S.CLASSES)); w = 0.36
    for i, (v, col, lab) in enumerate([(1, GREEN, "window = the detector's box (no ISP rules)"),
                                       (0, AMBER, "baseline ISP rules")]):
        m = [np.mean(res[(c, v)]) for c in S.CLASSES]; se = [np.std(res[(c, v)]) / np.sqrt(n) for c in S.CLASSES]
        b = ax[0].bar(xg + (i - 0.5) * w, m, w - 0.03, yerr=se, color=col, label=lab, capsize=2,
                      error_kw={"lw": 1, "ecolor": INK2})
        ax[0].bar_label(b, labels=[f"{x:.0f}" for x in m], padding=4, fontsize=9, color=INK)
    ax[0].set_xticks(xg); ax[0].set_xticklabels(S.LABELS, fontsize=9); ax[0].set_ylim(0, 112)
    ax[0].set_title("class detector → one PDAF window fitted to its box:\nin focus on the subject (%)", fontsize=10.5)
    ax[0].legend(frameon=False, fontsize=9, loc="lower left")
    yv = np.arange(len(VARIANTS))[::-1]
    m = [np.mean(res[(BIRD, v)]) for v in range(len(VARIANTS))]
    se = [np.std(res[(BIRD, v)]) / np.sqrt(n) for v in range(len(VARIANTS))]
    cols = [AMBER, GREEN] + [GREY] * (len(VARIANTS) - 2)
    b = ax[1].barh(yv, m, 0.7, xerr=se, color=cols, capsize=2, error_kw={"lw": 1, "ecolor": INK2})
    ax[1].bar_label(b, labels=[f"{x:.0f}" for x in m], padding=8, fontsize=9, color=INK)
    ax[1].set_yticks(yv); ax[1].set_yticklabels([nm for nm, _ in VARIANTS], fontsize=9); ax[1].set_xlim(0, 108)
    ax[1].set_title("bird, busy background: one ISP rule changed at a time (in focus, %)", fontsize=10.5)
    fig.suptitle(f"The ISP's window rules: what they cost ({n} seeds; generic placeholder values)",
                 fontsize=12.5, color=INK)
    fig.tight_layout(rect=[0, 0, 1, 0.94]); fig.savefig("out/isp_window.png", dpi=120)


def main():
    seeds = list(range(20)); t0 = time.time()
    jobs = [(s, cls, v) for cls in S.CLASSES for v in (0, 1) for s in seeds]
    jobs += [(s, BIRD, v) for v in range(2, len(VARIANTS)) for s in seeds]
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, jobs, chunksize=1)
    print(f"sim compute: {time.time()-t0:.0f}s ({len(jobs)} runs)\n")
    res = {}
    for s, cls, v, e in out:
        res.setdefault((cls, v), []).append(e)
    n = len(seeds)
    print(f"class detector -> one fitted PDAF window: in focus on the subject %, mean ± sem, {n} seeds")
    for cls in S.CLASSES:
        a, b = np.array(res[(cls, 1)]), np.array(res[(cls, 0)])
        print(f"   {cls:<16} no ISP rules {a.mean():5.1f} ± {a.std() / np.sqrt(n):.1f}   baseline ISP "
              f"{b.mean():5.1f} ± {b.std() / np.sqrt(n):.1f}   paired {np.mean(b - a):+.1f} ± {np.std(b - a) / np.sqrt(n):.1f}")
    print(f"\n{BIRD}, busy background: one rule changed at a time (paired difference from the baseline)")
    base = np.array(res[(BIRD, 0)])
    for v, (name, _) in enumerate(VARIANTS):
        a = np.array(res[(BIRD, v)])
        print(f"   {name:<46} {a.mean():5.1f} ± {a.std() / np.sqrt(n):.1f}   {np.mean(a - base):+5.1f} ± {np.std(a - base) / np.sqrt(n):.1f}")
    os.makedirs("out", exist_ok=True); figure(res, n); print("\nsaved out/isp_window.png")


if __name__ == "__main__":
    main()
