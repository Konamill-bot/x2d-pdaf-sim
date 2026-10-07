# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Two realism gaps of the 1-D AF chain, on the 4-phase timeline:

  A. CDAF verification. PDAF can lock confidently on a false plane (periodic texture, the
     harsh phase's "periodic" segments). DualGated(cdaf_verify=True) checks every new lock
     by contrast and hill-climbs contrast when it fails. Parameters set a priori, not tuned.
  B. PDAF gain calibration. The camera converts disparity to defocus with a gain K from a
     table; on a real body K moves with lens, aperture and temperature. The table is made
     wrong by -40..+40 %, with and without online self-calibration (pdaf_sim/calib.py).

A preflight check asserts that the default chain is still bit-identical to the simulator.
Run:  python scripts/run_realism_gaps.py  ->  out/realism_gaps.png   (~20 min)
"""
from __future__ import annotations
import importlib.util, os, sys, time
from multiprocessing import Pool
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_aft_tracker", os.path.join(HERE, "run_aft_tracker.py"))
T = importlib.util.module_from_spec(spec); sys.modules["run_aft_tracker"] = T; spec.loader.exec_module(T)
from pdaf_sim.policy_dual import DualGated
A, X = T.A, T.X
SEEDS_A = list(range(30, 40)); SEEDS_B = list(range(30, 36)); EPS = [-0.4, -0.2, 0.0, 0.2, 0.4]
C_AFC, C_AFT, C_REF = "#1D9E75", "#7B4FC9", "#8A8F98"
INK, INK2, GRID = "#222222", "#555555", "#E6E6E6"


def _job(job):
    kind = job[0]
    if kind == "pre":                                         # preflight: bit-identity
        _, which = job
        if which == "sim":
            return job, A.run("DualGated", X.SPEED_TARGET, 1, "afc", A.build_timeline(seed=30), seed=31), None
        return job, T.run_chain(30, "afc"), None
    if kind == "A":
        _, mode, cdaf, s = job
        return job, T.run_chain(s, mode, brain=DualGated(cdaf_verify=cdaf)), None
    _, eps, sc, s = job
    kl = []
    tr = T.run_chain(s, "afc", k0=0, k1=A.B2, ppm_scale=1.0 + eps, selfcal=sc, klog=kl)
    return job, tr, (kl[-1] if kl else None)


def main():
    os.makedirs("out", exist_ok=True)
    jobs = ([("pre", "sim"), ("pre", "chain")]
            + [("A", m, cd, s) for s in SEEDS_A for m in ["afc", "aft"] for cd in (False, True)]
            + [("B", e, sc, s) for s in SEEDS_B for e in EPS for sc in (False, True)])
    t0 = time.time()
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, jobs)
    res = {j: (tr, kf) for j, tr, kf in out}
    same = np.array_equal(res[("pre", "sim")][0], res[("pre", "chain")][0])
    print(f"sim compute: {time.time()-t0:.0f}s   preflight: default chain bit-identical to the simulator = {same}\n")
    assert same

    # ---- A: CDAF verification ----
    phase = np.zeros(A.N, dtype=int); phase[A.B1:A.B2] = 1; phase[A.B2:A.B3] = 2; phase[A.B3:] = 3
    cols = ["P1", "P2", "P3", "P4", "P4 periodic", "P4 other", "overall"]
    dA = {}
    for m, lab in [("afc", "AF-C"), ("aft", "AF-T")]:
        rows = {False: [], True: []}
        for s in SEEDS_A:
            tl = A.build_timeline(seed=s); ts = np.array([X.focus_mm(d) for d in tl[0]])
            tg = A.afc_target_mm(tl[0], tl[5], tl[6]) if m == "afc" else ts
            per = (phase == 3) & (tl[4] == "periodic")
            for cd in (False, True):
                e = np.abs(res[("A", m, cd, s)][0] - tg) < X.DEADBAND
                rows[cd].append([100 * e[phase == p].mean() for p in range(4)]
                                + [100 * e[per].mean() if per.any() else np.nan,
                                   100 * e[(phase == 3) & ~per].mean(), 100 * e.mean()])
        off, on = np.array(rows[False]), np.array(rows[True]); d = on - off
        dA[m] = (np.nanmean(d, 0), np.nanstd(d, 0) / np.sqrt(len(SEEDS_A)))
        print(f"A. CDAF verification, {lab} (seeds {SEEDS_A[0]}-{SEEDS_A[-1]}, in-focus %)")
        print(f"   {'':<16}" + "".join(f"{c:>13}" for c in cols))
        print(f"   {'PDAF only':<16}" + "".join(f"{v:>13.1f}" for v in np.nanmean(off, 0)))
        print(f"   {'+ CDAF verify':<16}" + "".join(f"{v:>13.1f}" for v in np.nanmean(on, 0)))
        print(f"   {'diff (sem)':<16}" + "".join(f"{a:>+8.1f}±{b:<4.1f}" for a, b in zip(*dA[m])) + "\n")

    # ---- B: gain calibration ----
    print(f"B. PDAF gain table off by eps, AF-C, P1 + P2 (seeds {SEEDS_B[0]}-{SEEDS_B[-1]})")
    print(f"   {'eps':>6}  {'table: P1':>10} {'P2':>6}   {'self-cal: P1':>13} {'P2':>6}   {'K error after':>14}")
    curve = {False: [], True: []}
    for eps in EPS:
        line = f"   {eps:>+6.0%}  "
        for sc in (False, True):
            p1 = []; p2 = []; kerr = []
            for s in SEEDS_B:
                tl = A.build_timeline(seed=s); ts = np.array([X.focus_mm(d) for d in tl[0]])[:A.B2]
                tr, kf = res[("B", eps, sc, s)]
                e = np.abs(tr - ts) < X.DEADBAND
                p1.append(100 * e[:A.B1].mean()); p2.append(100 * e[A.B1:A.B2].mean())
                if kf is not None: kerr.append(100 * (kf / A.PPM - 1))
            curve[sc].append((np.mean(p1), np.std(p1) / np.sqrt(len(p1)), np.mean(p2)))
            line += (f"{np.mean(p1):>10.1f} {np.mean(p2):>6.1f}   " if not sc else
                     f"{np.mean(p1):>13.1f} {np.mean(p2):>6.1f}   {np.mean(kerr):>+12.1f}%")
        print(line)

    # ---- figure ----
    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.titlecolor": INK, "font.size": 10})
    fig, ax = plt.subplots(1, 2, figsize=(15.5, 5.4), gridspec_kw={"width_ratios": [1.3, 1]})
    for a in ax:
        a.grid(axis="y", color=GRID, lw=1); a.set_axisbelow(True)
        for sp in ("top", "right"): a.spines[sp].set_visible(False)
    xg = np.arange(len(cols)); w = 0.36
    for i, (m, col, lab) in enumerate([("afc", C_AFC, "AF-C"), ("aft", C_AFT, "AF-T")]):
        mm, ss = dA[m]
        b = ax[0].bar(xg + (i - 0.5) * w, mm, w * 0.92, yerr=ss, color=col, label=lab, capsize=3,
                      error_kw={"lw": 1, "ecolor": INK2})
        j = cols.index("P4 periodic")
        ax[0].text(b[j].get_x() + b[j].get_width() / 2, mm[j] + ss[j] + 0.4, f"{mm[j]:+.1f}", ha="center", fontsize=9, color=INK)
    ax[0].axhline(0, color=INK2, lw=1); ax[0].set_xticks(xg); ax[0].set_xticklabels(cols, fontsize=9)
    ax[0].set_ylabel("Δ in-focus points, + CDAF verify − PDAF only (mean ± sem)")
    ax[0].set_title(f"A. Confirming each new lock by contrast (n={len(SEEDS_A)} seeds)")
    ax[0].legend(fontsize=9, frameon=False, loc="upper left")
    xe = [100 * e for e in EPS]
    for sc, col, lab in [(False, C_REF, "gain from the (wrong) table"), (True, C_AFC, "+ online self-calibration")]:
        m1 = [c[0] for c in curve[sc]]; s1 = [c[1] for c in curve[sc]]
        ax[1].errorbar(xe, m1, yerr=s1, color=col, lw=2, marker="o", ms=7, mec="white", mew=1.5, capsize=3, label=lab)
    ax[1].set_xlabel("PDAF gain table error (%)"); ax[1].set_ylabel("P1 in-focus % (mean ± sem)")
    ax[1].set_title(f"B. Gain calibration error, AF-C, P1 random teleports (n={len(SEEDS_B)})")
    ax[1].legend(fontsize=9, frameon=False, loc="lower center")
    fig.suptitle("Realism gaps of the 1-D chain: false PDAF locks and PDAF gain calibration", fontsize=13, color=INK)
    fig.tight_layout(rect=[0, 0, 1, 0.94]); fig.savefig("out/realism_gaps.png", dpi=120)
    print("\nsaved out/realism_gaps.png")


if __name__ == "__main__":
    main()
