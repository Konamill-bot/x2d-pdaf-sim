# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Lens speed cap 4000 (X2D today) vs 10000 steps/s (proposed) for the current AF chain:
DualGated AF-C and AF-T with the real ZoneTracker, seeds 30-49, 4-phase timeline. Reports
in-focus % per phase and time-to-focus after the P1 jumps and AF-C occluder switches.
STEPS_PER_MM (run_x2d_plus) is an assumption: read the relative 2.5x, not absolute mm/s.

Run from the repo root:  python scripts/run_speed_cap_chain.py   (~12 min)
"""
import sys, os, importlib.util, time, numpy as np
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_aft_tracker", os.path.join(HERE, "run_aft_tracker.py"))
T = importlib.util.module_from_spec(spec); sys.modules["run_aft_tracker"] = T; spec.loader.exec_module(T)
A, X = T.A, T.X
SPEEDS = {"4000 (X2D now)": X.SPEED_X2D, "10000 (proposed)": X.SPEED_TARGET}

def job(a):
    s, sp, mode = a
    X.SPEED_TARGET = SPEEDS[sp]                 # run_chain drives the motor at X.SPEED_TARGET
    return s, sp, mode, T.run_chain(s, mode)

def settle_times(track, tgt, starts, need=3):
    out = []
    for k in starts:
        ok = np.abs(track[k:] - tgt[k:]) < X.DEADBAND
        run = np.convolve(ok.astype(int), np.ones(need, dtype=int), "valid")
        j = int(np.argmax(run >= need)) if np.any(run >= need) else len(ok)
        out.append(j / 60.0)
    return np.array(out)

if __name__ == "__main__":
    seeds = list(range(30, 50))
    t0 = time.time()
    with Pool(os.cpu_count()) as p:
        out = p.map(job, [(s, sp, m) for s in seeds for sp in SPEEDS for m in ["afc", "aft"]])
    tr = {(s, sp, m): t for s, sp, m, t in out}
    print(f"sim compute {time.time()-t0:.0f}s, seeds 30-49, vmax: 4000 -> {X.SPEED_X2D/X.STEPS_PER_MM:.1f} mm/s, "
          f"10000 -> {10000/X.STEPS_PER_MM:.1f} mm/s (STEPS_PER_MM={X.STEPS_PER_MM:.0f} is an assumption)\n")
    phase = np.zeros(A.N, dtype=int); phase[A.B1:A.B2] = 1; phase[A.B2:A.B3] = 2; phase[A.B3:] = 3
    cols = ["P1", "P2", "P3", "P4", "overall", "occluded", "hidden"]
    score = {}; jumps = {}; switch = {}
    for m in ["afc", "aft"]:
        for sp in SPEEDS:
            rows = []; js = []; sw = []
            for s in seeds:
                tl = A.build_timeline(seed=s); subj, occ = tl[0], tl[5]
                ts = np.array([X.focus_mm(d) for d in subj])
                tg = A.afc_target_mm(subj, occ, tl[6]) if m == "afc" else ts
                e = np.abs(tr[(s, sp, m)] - tg) < X.DEADBAND; p3 = phase == 2
                rows.append([100 * e[phase == p].mean() for p in range(4)] + [100 * e.mean(),
                             100 * e[p3 & (occ > 0.5)].mean(), 100 * e[p3 & (occ >= 1)].mean()])
                # P1 teleports: settle time after each jump, with its size
                ks = [k for k in range(1, A.B1) if subj[k] != subj[k - 1]]
                st = settle_times(tr[(s, sp, m)], tg, ks)
                js += [(abs(ts[k] - ts[k - 1]), t) for k, t in zip(ks, st)]
                if m == "afc":                         # AF-C: time to switch onto an occluder
                    on = [k for k in range(A.B2 + 1, A.B3) if occ[k] > 0.5 and occ[k - 1] <= 0.5]
                    sw += list(settle_times(tr[(s, sp, m)], tg, on))
            score[(m, sp)] = np.array(rows); jumps[(m, sp)] = np.array(js); switch[(m, sp)] = np.array(sw)
    for m, lab in [("afc", "AF-C (DualGated, scored on the AF area)"), ("aft", "AF-T (real tracker, scored on the subject)")]:
        print(lab)
        print(f"   {'speed cap':<20}" + "".join(f"{c:>12}" for c in cols))
        for sp in SPEEDS:
            r = score[(m, sp)]; print(f"   {sp:<20}" + "".join(f"{v:>12.1f}" for v in r.mean(0)))
        d = score[(m, "10000 (proposed)")] - score[(m, "4000 (X2D now)")]
        print(f"   {'10000 - 4000 (sem)':<20}" + "".join(f"{a:>+7.1f}±{b:<4.1f}" for a, b in zip(d.mean(0), d.std(0) / np.sqrt(len(seeds)))))
        print()
    print("P1 random teleports: time to focus (3 frames in focus) after each jump, AF-C")
    for lo, hi in [(0.0, 0.5), (0.5, 1.5), (1.5, 3.0), (3.0, 9.0)]:
        line = f"   jump {lo:.1f}-{hi:.1f} mm: "
        for sp in SPEEDS:
            j = jumps[("afc", sp)]; sel = (j[:, 0] >= lo) & (j[:, 0] < hi)
            if sel.sum(): line += f"{sp.split()[0]:>6}: median {1000*np.median(j[sel, 1]):4.0f} ms, p90 {1000*np.percentile(j[sel, 1], 90):4.0f} ms (n={sel.sum()})   "
        print(line)
    print("\nAF-C switching onto an occluder that fills the AF area (P3): time to focus on it")
    for sp in SPEEDS:
        w = switch[("afc", sp)]; print(f"   {sp:<20} median {1000*np.median(w):4.0f} ms, p90 {1000*np.percentile(w, 90):4.0f} ms (n={len(w)})")
