# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""AF-T with a REAL subject tracker (ZoneTracker) vs the ideal tracker, on the 4-phase timeline.

The AF chain per frame, each stage swappable (af_c/demo.py plugs in the C versions):

    PDAF views --eyes--> per-zone (disparity, confidence) --tracker--> subject zones
               --combine--> one measurement --brain (DualGated)--> lens command

  * AF-C        : no tracker, every zone is combined (identical to run_ablation_4phase).
  * AF-T ideal  : the simulator's ideal tracker (it is told which zones the occluder covers).
  * AF-T real   : ZoneTracker, which sees only per-zone depth + confidence.

AF-C is scored against what fills the AF area, AF-T against the subject. ZoneTracker was
developed on seeds 100-114. The reported numbers are seeds 30-49, which were not looked at
until the design was frozen. (Seeds 0-9 and 20-29 were used to evaluate an earlier version,
so they no longer count as held out; see DEV_LOG, "ZoneTracker".)

Run:  python scripts/run_aft_tracker.py [--quick]  ->  out/aft_tracker.png  (~15 min; --quick ~3 min)
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
from pdaf_sim.policy_dual import DualGated
from pdaf_sim.zone_tracker import ZoneTracker, zone_estimates, combine_zones
X = A.X
X.DualGated = DualGated
NZ, PPM, ALIAS = A.NZ, A.PPM, A.ALIAS


def py_eyes(L, R):
    return zone_estimates(L, R, NZ, X.MAX_DISP_PX)


def wrap(d_mm):
    """The simulator's periodic-pattern aliasing: phase wraps at ALIAS mm."""
    return ((d_mm + ALIAS / 2) % ALIAS) - ALIAS / 2


def run_chain(seed, mode, brain=None, tracker=None, eyes=py_eyes, k0=0, k1=None, log=False):
    """Closed loop over frames [k0, k1) of the 4-phase timeline. mode: "afc" (all zones)
    or "aft" (zones chosen by `tracker`). Returns the lens trajectory (and a per-frame
    hold log for AF-T when log=True). RNG use matches run_ablation_4phase.run."""
    k1 = A.N if k1 is None else k1
    subj, con, bright, fog, dist, occ, occ_d, rain = A.build_timeline(seed=seed)
    rng = np.random.default_rng(seed + 1); nrng = np.random.default_rng(seed + 8)
    tex = (high_contrast(rng), high_contrast(rng), A.rain_texture(rng))
    brain = DualGated() if brain is None else brain
    if mode == "aft" and tracker is None:
        tracker = ZoneTracker()
    lb = X.LatencyBuffer(X.LATENCY)
    lens = X.focus_mm(subj[k0]); last = lens; motor = X.make_motor(lens, X.SPEED_TARGET)
    track = np.zeros(k1 - k0); held = np.zeros(k1 - k0, dtype=bool)
    for k in range(k0, k1):
        tgt = X.focus_mm(subj[k])
        accum = 4 if (con[k] < 0.1 or fog[k] > 0.3 or bright[k] < 0.5) else 1
        v = A.render_views(tex, lens, tgt, con[k], bright[k], fog[k], dist[k], occ[k], occ_d[k],
                           rain[k], accum, nrng, rng)
        meas = None if v is None else (*eyes(v[0], v[1]), dist[k])
        arr = lb.push_pop((meas, lens))                       # lens at exposure travels with it
        if arr is None or arr[0] is None:
            last = brain.coast()
        else:
            (dpx, cz, dk), lens_m = arr
            if mode == "afc":
                d, c = combine_zones(dpx, cz); d_mm = d / PPM
                if dk == "periodic":
                    d_mm = wrap(d_mm); c = max(c, 0.8)
                last = brain.step(d_mm, c, 0.0, lens_m)
            else:
                d_mm = np.asarray(dpx) / PPM; cz = np.asarray(cz, dtype=float)
                if dk == "periodic":
                    d_mm = wrap(d_mm); cz = np.maximum(cz, 0.8)
                sel = tracker.select(lens_m - d_mm, cz, brain.predict())
                if sel is None:
                    last = brain.coast(); held[k - k0] = True
                else:
                    use = sel if np.any(sel) else np.ones(NZ, dtype=bool)
                    d, c = combine_zones(d_mm[use] * PPM, cz[use])
                    last = brain.step(d / PPM, c, 0.0, lens_m)
        lens = motor.command(last); track[k - k0] = lens
    return (track, held) if log else track


# ---- evaluation ----------------------------------------------------------------
QUICK = "--quick" in sys.argv
TUNE = "--tune" in sys.argv
SEEDS = list(range(100, 115)) if TUNE else ([30, 31, 32, 33] if QUICK else list(range(30, 50)))
CFGS = [("AF-C", "afc"), ("AF-T ideal tracker", "aft-ideal"), ("AF-T real tracker", "aft-real")]
C_AFC, C_IDEAL, C_REAL = "#1D9E75", "#7B4FC9", "#E0A030"
INK, INK2, GRID = "#222222", "#555555", "#E6E6E6"


def _job(a):
    ci, s = a
    kind = CFGS[ci][1]
    if kind == "aft-ideal":
        return ci, s, A.run("DualGated", X.SPEED_TARGET, 1, "aft", A.build_timeline(seed=s), seed=s + 1), None
    tr = run_chain(s, "afc" if kind == "afc" else "aft", log=True)
    return ci, s, tr[0], tr[1]


def main():
    os.makedirs("out", exist_ok=True)
    t0 = time.time()
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, [(ci, s) for s in SEEDS for ci in range(len(CFGS))])
    res = {(ci, s): (tr, h) for ci, s, tr, h in out}
    print(f"sim compute: {time.time()-t0:.0f}s ({len(SEEDS)} seeds x {len(CFGS)} configs)\n")
    phase = np.zeros(A.N, dtype=int); phase[A.B1:A.B2] = 1; phase[A.B2:A.B3] = 2; phase[A.B3:] = 3
    cols = ["P1", "P2", "P3", "P4", "overall", "occluded", "partial", "hidden"]
    score = {}
    for ci, (name, kind) in enumerate(CFGS):
        rows = []
        for s in SEEDS:
            tl = A.build_timeline(seed=s)
            ts = np.array([X.focus_mm(d) for d in tl[0]])
            tg = A.afc_target_mm(tl[0], tl[5], tl[6]) if kind == "afc" else ts
            e = np.abs(res[(ci, s)][0] - tg); occ = tl[5]
            p3 = phase == 2
            rows.append([100 * np.mean(e[phase == p] < X.DEADBAND) for p in range(4)]
                        + [100 * np.mean(e < X.DEADBAND),
                           100 * np.mean(e[p3 & (occ > 0.5)] < X.DEADBAND),          # occluder fills AF area
                           100 * np.mean(e[p3 & (occ > 0) & (occ < 1)] < X.DEADBAND),  # partly covered
                           100 * np.mean(e[p3 & (occ >= 1)] < X.DEADBAND)])            # subject fully hidden
        score[ci] = np.array(rows)
    print(f"seeds {SEEDS}: in-focus % (mean ± std); AF-C scored vs the AF area, AF-T vs the subject")
    print(f"   {'':<22}" + "".join(f"{c:>13}" for c in cols))
    for ci, (name, _) in enumerate(CFGS):
        r = score[ci]
        print(f"   {name:<22}" + "".join(f"{m:>7.1f}±{s:<5.1f}" for m, s in zip(r.mean(0), r.std(0))))
    d = score[2] - score[1]
    print(f"   {'real - ideal (sem)':<22}" + "".join(f"{m:>+7.1f}±{s:<5.1f}" for m, s in zip(d.mean(0), d.std(0) / np.sqrt(len(SEEDS)))))
    # how often the real tracker held (coasted) while the subject was actually visible
    vis_hold = []
    for s in SEEDS:
        tl = A.build_timeline(seed=s); occ = tl[5]; held = res[(2, s)][1]
        vis_hold.append(100 * np.mean(held[(occ == 0)]))
    print(f"\n   real tracker held while no occluder was present: {np.mean(vis_hold):.2f}% of frames")

    # ---- figure ----
    s0 = SEEDS[0]
    tl = A.build_timeline(seed=s0); subj, occ, occ_d = tl[0], tl[5], tl[6]
    a, b = A.B2 + 4 * 60, A.B2 + 16 * 60                          # 12 s of P3
    t = np.arange(a, b) / 60.0
    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.titlecolor": INK, "font.size": 10})
    fig, ax = plt.subplots(2, 1, figsize=(15, 10.5), gridspec_kw={"height_ratios": [1.15, 1]})
    for x in ax:
        x.grid(axis="y", color=GRID, lw=1); x.set_axisbelow(True)
        for sp in ("top", "right"): x.spines[sp].set_visible(False)
    d_ = np.diff(np.r_[0, (occ[a:b] > 0).astype(int), 0])
    for i0, i1 in zip(np.where(d_ == 1)[0], np.where(d_ == -1)[0]):
        ax[0].axvspan(t[i0], t[min(i1, len(t) - 1)], color="#D9DCE1", lw=0)
    oc = np.where(occ[a:b] > 0, [X.focus_mm(v) if v > 0 else np.nan for v in occ_d[a:b]], np.nan)
    ax[0].plot(t, oc, color=INK2, lw=2, ls=":", label="occluder depth (grey band: in front)")
    ax[0].plot(t, [X.focus_mm(v) for v in subj[a:b]], color=INK, lw=2, label="subject")
    for ci, col, lw in [(0, C_AFC, 1.6), (1, C_IDEAL, 1.6), (2, C_REAL, 2.2)]:
        ax[0].plot(t, res[(ci, s0)][0][a:b], color=col, lw=lw, label=f"lens: {CFGS[ci][0]}")
    ax[0].set_ylabel("lens position (mm)"); ax[0].set_xlabel("time (s)")
    ax[0].set_title(f"P3 occlusion, seed {s0}: AF-C follows the occluder; AF-T (ideal and real) stays on the subject")
    ax[0].legend(fontsize=8.5, frameon=False, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    groups = ["P1", "P2", "P3", "P4", "overall", "occluder fills\nAF area", "subject\npartly covered",
              "subject\nfully hidden"]
    xg = np.arange(len(groups)); w = 0.26
    for ci, col in [(0, C_AFC), (1, C_IDEAL), (2, C_REAL)]:
        m = score[ci].mean(0); s = score[ci].std(0) / np.sqrt(len(SEEDS))
        ax[1].bar(xg + (ci - 1) * w, m, w * 0.92, yerr=s, color=col, capsize=2, label=CFGS[ci][0],
                  error_kw={"lw": 1, "ecolor": INK2})
    for j in (5, 6, 7):
        m = score[2].mean(0)[j]
        ax[1].text(xg[j] + w, m + 2, f"{m:.0f}", ha="center", fontsize=8.5, color=INK)
    ax[1].set_xticks(xg); ax[1].set_xticklabels(groups, fontsize=9); ax[1].set_ylim(0, 122)
    ax[1].set_ylabel("in-focus % (mean ± sem)")
    ax[1].set_title(f"Seeds {SEEDS[0]}-{SEEDS[-1]} (n={len(SEEDS)}, not used in development): "
                    "AF-C scored on the AF area, AF-T on the subject")
    ax[1].legend(fontsize=9, frameon=False, ncol=3, loc="upper left")
    fig.suptitle("AF-T with a real depth-only subject tracker vs the ideal tracker", fontsize=13, color=INK)
    fig.tight_layout(rect=[0, 0, 1, 0.97]); fig.savefig("out/aft_tracker.png", dpi=120)
    print("saved out/aft_tracker.png")


if __name__ == "__main__":
    main()
