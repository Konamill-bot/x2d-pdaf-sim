# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""DualGated vs X2D+: confidence calibration, P2 headroom, held-out comparison.

  A. Calibration. Robust sigma of the PDAF depth error vs PSR confidence at 1-2%
     contrast (4-frame integration, as in P2), and the fit sigma(c) = S0 * c^-P
     that pdaf_sim.policy_dual.DualGated uses. Also shown: the sigma implied by
     X2D+'s R = 1/c.
  B. P2 headroom (P2 slice, seeds 0-9). X2D+, DualGated, X2D+ fed a PERFECT
     measurement (zero error, confidence 1), and perfect + zero latency. The
     perfect-measurement bar is the most any confidence handling can recover.
  C. Paired difference DualGated - X2D+ per phase, AF-C and AF-T, on held-out
     seeds 0-9 and 20-29 (the full 4-phase timeline of run_ablation_4phase).
     DualGated's parameters were tuned on seeds 100-109 only.

Run:  python scripts/run_dual_gated.py [--quick]  ->  out/dual_gated.png  (~15 min; --quick ~3 min)
"""
from __future__ import annotations
import importlib.util, os, sys, time
from multiprocessing import Pool
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_ablation_4phase", os.path.join(HERE, "run_ablation_4phase.py"))
A = importlib.util.module_from_spec(spec); sys.modules["run_ablation_4phase"] = A; spec.loader.exec_module(A)
from pdaf_sim.scene import high_contrast
from pdaf_sim.policy_dual import DualGated, S0, P
X = A.X
X.DualGated = DualGated                    # A.run resolves policies by name on run_x2d_plus

QUICK = "--quick" in sys.argv
HELD = [0, 1, 2, 3] if QUICK else list(range(10)) + list(range(20, 30))
P2_SEEDS = [0, 1, 2, 3] if QUICK else list(range(10))
CFGS = [("X2D+", "X2DPlus", "afc"), ("X2D+", "X2DPlus", "aft"),
        ("DualGated", "DualGated", "afc"), ("DualGated", "DualGated", "aft")]
C_AFC, C_AFT, C_REF = "#1D9E75", "#7B4FC9", "#8A8F98"
INK, INK2, GRID = "#222222", "#555555", "#E6E6E6"


# ---- A. confidence calibration ----------------------------------------------
def calibrate(n=4000):
    """PDAF depth error vs confidence at P2 conditions (same measure() as the ablation)."""
    rng = np.random.default_rng(5); nrng = np.random.default_rng(6)
    rows = []
    for t in range(n):
        if t % 400 == 0:
            tex = (high_contrast(rng), high_contrast(rng), A.rain_texture(rng))
        tgt = X.focus_mm(rng.uniform(1.0, 4.0))
        lens_err = rng.choice([rng.normal(0, 0.1), rng.uniform(-1.5, 1.5)])   # near focus / far
        d, c, _ = A.measure(tex, tgt + lens_err, tgt, rng.uniform(0.01, 0.02), 1.0, 0.0, "",
                            0.0, 0.0, 0.0, 4, nrng, rng)
        rows.append((c, (tgt + lens_err - d) - tgt))
    c, e = np.array(rows).T
    xs, ys, ns = [], [], []
    for lo in np.arange(0.1, 1.0, 0.1):
        m = (c >= lo) & (c < lo + 0.1 + (0.01 if lo > 0.85 else 0))
        if m.sum() < 20:
            continue
        xs.append(np.median(c[m])); ns.append(int(m.sum()))
        ys.append(1.4826 * np.median(np.abs(e[m] - np.median(e[m]))))         # robust sigma
    slope, icpt = np.polyfit(np.log(xs), np.log(ys), 1)
    return np.array(xs), np.array(ys), np.array(ns), float(np.exp(icpt)), float(-slope)


# ---- B. P2 headroom ---------------------------------------------------------
def run_p2(policy, seed, oracle=False, lat=X.LATENCY):
    """P2 slice only (starts in focus). oracle: perfect measurement (zero error, confidence 1)."""
    subj, con, bright, fog, dist, occ, occ_d, rain = A.build_timeline(seed=seed)
    rng = np.random.default_rng(seed + 1); nrng = np.random.default_rng(seed + 8)
    tex = (high_contrast(rng), high_contrast(rng), A.rain_texture(rng))
    pol = getattr(X, policy)(); lb = X.LatencyBuffer(lat)
    lens = X.focus_mm(subj[A.B1]); last = lens; motor = X.make_motor(lens, X.SPEED_TARGET)
    err = np.zeros(A.B2 - A.B1)
    for k in range(A.B1, A.B2):
        tgt = X.focus_mm(subj[k])
        if oracle:
            m = (lens - tgt, 1.0, 0.0)
        else:
            m = A.measure(tex, lens, tgt, con[k], bright[k], fog[k], dist[k], occ[k], occ_d[k], rain[k],
                          4, nrng, rng)                                         # P2: con < 0.1 -> 4
        arr = lb.push_pop((m[0], m[1], m[2], lens)) if lat > 0 else (m[0], m[1], m[2], lens)
        last = pol.coast() if (arr is None or arr[0] is None) else pol.step(arr[0], arr[1], arr[2], arr[3])
        lens = motor.command(last); err[k - A.B1] = abs(lens - tgt)
    return 100 * np.mean(err < X.DEADBAND)

P2_VARIANTS = [("X2D+", "X2DPlus", False, X.LATENCY), ("DualGated", "DualGated", False, X.LATENCY),
               ("X2D+ with\nperfect meas.", "X2DPlus", True, X.LATENCY),
               ("perfect meas.\n+ zero latency", "X2DPlus", True, 0)]


# ---- jobs ------------------------------------------------------------------
def _job(job):
    kind = job[0]
    if kind == "cal":
        return job, calibrate()
    if kind == "p2":
        _, vi, seed = job; _, pol, oracle, lat = P2_VARIANTS[vi]
        return job, run_p2(pol, seed, oracle, lat)
    _, ci, seed = job; _, pol, mode = CFGS[ci]
    return job, A.run(pol, X.SPEED_TARGET, 1, mode, A.build_timeline(seed=seed), seed=seed + 1)


def main():
    os.makedirs("out", exist_ok=True)
    jobs = ([("cal",)] + [("full", ci, s) for s in HELD for ci in range(len(CFGS))]
            + [("p2", vi, s) for s in P2_SEEDS for vi in range(len(P2_VARIANTS))])
    t0 = time.time()
    with Pool(os.cpu_count()) as pool:
        res = dict(pool.map(_job, jobs))
    print(f"sim compute: {time.time()-t0:.0f}s ({len(HELD)} held-out seeds x {len(CFGS)} configs, "
          f"{len(P2_SEEDS)} P2 seeds x {len(P2_VARIANTS)} variants, calibration)\n")

    # A
    cx, cy, cn, s0_fit, p_fit = res[("cal",)]
    print(f"A. calibration fit: sigma(c) = {s0_fit:.4f} * c^-{p_fit:.3f} mm   "
          f"(DualGated uses the rounded S0={S0}, P={P})")
    for x_, y_, n_ in zip(cx, cy, cn):
        print(f"   conf {x_:.2f}  n={n_:4d}  sigma {y_:.3f} mm   (X2D+ implies {1/np.sqrt(x_):.2f} mm)")

    # B
    p2 = {v[0]: np.array([res[("p2", vi, s)] for s in P2_SEEDS]) for vi, v in enumerate(P2_VARIANTS)}
    print("\nB. P2 slice in-focus %, seeds", P2_SEEDS)
    for k, v in p2.items():
        print(f"   {k.replace(chr(10), ' '):<38} {v.mean():5.1f} ± {v.std():4.1f}")

    # C
    phase = np.zeros(A.N, dtype=int); phase[A.B1:A.B2] = 1; phase[A.B2:A.B3] = 2; phase[A.B3:] = 3
    cols = ["P1", "P2", "P3", "P4", "overall", "occluded"]
    score = {}
    for ci, (name, _, mode) in enumerate(CFGS):
        rows = []
        for s in HELD:
            tl = A.build_timeline(seed=s)
            ts = np.array([X.focus_mm(d) for d in tl[0]])
            tg = A.afc_target_mm(tl[0], tl[5], tl[6]) if mode == "afc" else ts
            e = np.abs(res[("full", ci, s)] - tg)
            big = (phase == 2) & (tl[5] > 0.5)
            rows.append([100 * np.mean(e[phase == p] < X.DEADBAND) for p in range(4)]
                        + [100 * np.mean(e < X.DEADBAND), 100 * np.mean(e[big] < X.DEADBAND)])
        score[(name, mode)] = np.array(rows)
    print(f"\nC. held-out seeds {HELD}: in-focus % (mean ± std) and paired difference")
    print(f"   {'':<22}" + "".join(f"{c:>13}" for c in cols))
    for (name, mode), r in score.items():
        print(f"   {name + ' ' + mode.upper():<22}" + "".join(f"{m:>7.1f}±{s:<5.1f}" for m, s in zip(r.mean(0), r.std(0))))
    diffs = {}
    for mode in ["afc", "aft"]:
        d = score[("DualGated", mode)] - score[("X2D+", mode)]
        diffs[mode] = (d.mean(0), d.std(0) / np.sqrt(len(HELD)))
        print(f"   {'Dual - X2D+ ' + mode.upper():<22}" + "".join(
            f"{m:>+7.1f}±{s:<5.1f}" for m, s in zip(*diffs[mode])) + "  (mean ± sem)")
    print(f"   seeds where Dual P2 >= X2D+ P2 (AF-C): "
          f"{int(np.sum(score[('DualGated', 'afc')][:, 1] >= score[('X2D+', 'afc')][:, 1]))}/{len(HELD)}")

    # ---- figure ----
    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.titlecolor": INK, "font.size": 10})
    fig, ax = plt.subplots(1, 3, figsize=(17.5, 5.6), gridspec_kw={"width_ratios": [1.0, 1.0, 1.45]})
    for a in ax:
        a.grid(axis="y", color=GRID, lw=1); a.set_axisbelow(True)
        for sp in ("top", "right"): a.spines[sp].set_visible(False)
    # A
    cc = np.linspace(0.08, 1.0, 100)
    ax[0].plot(cc, 1 / np.sqrt(cc), color=C_REF, lw=2, ls=":", label="X2D+ assumption (R = 1/c)")
    ax[0].plot(cc, S0 * cc ** -P, color=C_AFC, lw=2, label=f"DualGated fit: {S0} · c^-{P}")
    ax[0].plot(cx, cy, "o", ms=7, color=INK, mec="white", mew=1.5, label="measured (robust σ)")
    ax[0].set_yscale("log"); ax[0].set_xlim(0, 1.02)
    ax[0].set_xlabel("PDAF confidence (PSR)"); ax[0].set_ylabel("depth error σ (mm, log)")
    ax[0].set_title("A. Confidence → measurement noise (1–2% contrast)")
    ax[0].legend(fontsize=8.5, frameon=False, loc="center right")
    # B
    names = list(p2); vals = [p2[k].mean() for k in names]; errs = [p2[k].std() / np.sqrt(len(P2_SEEDS)) for k in names]
    colors = [C_REF, C_AFC, C_REF, C_REF]
    for i, (v, e, col) in enumerate(zip(vals, errs, colors)):   # dots, not bars: the axis is not zero-based
        hyp = i >= 2                                              # hollow = hypothetical bound
        ax[1].errorbar(i, v, yerr=e, fmt="o", ms=9, color=col, mfc="white" if hyp else col,
                       mec=col if hyp else "white", mew=2, ecolor=INK2, elinewidth=1, capsize=3)
        ax[1].text(i + 0.12, v, f"{v:.1f}", va="center", fontsize=9, color=INK)
    ax[1].set_xticks(range(len(names))); ax[1].set_xticklabels(names, fontsize=8.5)
    ax[1].set_xlim(-0.5, len(names) - 0.3)
    ax[1].set_ylim(max(0, min(vals) - 6), 100); ax[1].set_ylabel("P2 in-focus % (mean ± sem)")
    ax[1].set_title(f"B. P2 headroom: even a perfect measurement\nadds only {vals[2]-vals[0]:+.1f}; latency is the rest")
    # C
    xg = np.arange(len(cols)); w = 0.36
    for i, (mode, col, lab) in enumerate([("afc", C_AFC, "AF-C"), ("aft", C_AFT, "AF-T")]):
        m, s = diffs[mode]
        bb = ax[2].bar(xg + (i - 0.5) * w, m, w * 0.92, yerr=s, color=col, label=lab, capsize=3,
                       error_kw={"lw": 1, "ecolor": INK2})
        for j in (1, 3, 5):                                                    # label the story: P2, P4, occluded
            y = m[j] + (s[j] + 0.25 if m[j] >= 0 else -s[j] - 0.6)
            ax[2].text(bb[j].get_x() + bb[j].get_width() / 2, y, f"{m[j]:+.1f}", ha="center", fontsize=8.5, color=INK)
    ax[2].axhline(0, color=INK2, lw=1)
    top = max(float(np.max(m + s)) for m, s in diffs.values())
    ax[2].set_ylim(top=top + 0.9)                                              # room for the labels
    ax[2].set_xticks(xg); ax[2].set_xticklabels(["P1\nteleport", "P2\nlow contrast", "P3\nocclusion",
                                                 "P4\nharsh + rain", "overall", "while occluder\nfills AF area"], fontsize=8.5)
    ax[2].set_ylabel("Δ in-focus points, DualGated − X2D+ (mean ± sem)")
    ax[2].set_title(f"C. Held-out seeds (n={len(HELD)}, tuned on 100–109): paired difference")
    ax[2].legend(fontsize=9, frameon=False, loc="upper left")
    fig.suptitle("DualGated: calibrated confidence, innovation gate, two-timescale coasting  vs  X2D+",
                 fontsize=13, color=INK)
    fig.tight_layout(rect=[0, 0, 1, 0.95]); fig.savefig("out/dual_gated.png", dpi=120)
    print("\nsaved out/dual_gated.png")


if __name__ == "__main__":
    main()
