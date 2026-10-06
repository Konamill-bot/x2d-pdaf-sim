# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Closed-loop check: the full 4-phase simulator driven by the Python reference vs the C
float build (what would run on a target), scored per phase.

Closed loop, a 1e-6 mm difference can eventually flip one discrete decision (gate,
confirm, sweep) and the two trajectories fork, so per-seed scores differ slightly in
both directions even for the double build; compare the means.
Run (after `make`):  python3 af_c/closed_loop.py [n_seeds]
"""
import importlib.util, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
from multiprocessing import Pool
import numpy as np
spec = importlib.util.spec_from_file_location("run_ablation_4phase", os.path.join(ROOT, "scripts", "run_ablation_4phase.py"))
A = importlib.util.module_from_spec(spec); sys.modules["run_ablation_4phase"] = A; spec.loader.exec_module(A)
from pdaf_sim.policy_dual import DualGated
from afdg import CDualGated
X = A.X
X.DualGated = DualGated
X.CDualGatedFloat = lambda: CDualGated(double=False)
IMPLS = {"Python": "DualGated", "C float": "CDualGatedFloat"}


def _job(a):
    s, impl, mode = a
    return s, impl, mode, A.run(IMPLS[impl], X.SPEED_TARGET, 1, mode, A.build_timeline(seed=s), seed=s + 1)


def main():
    seeds = list(range(int(sys.argv[1]) if len(sys.argv) > 1 else 4))
    t0 = time.time()
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, [(s, i, m) for s in seeds for i in IMPLS for m in ["afc", "aft"]])
    tr = {(s, i, m): t for s, i, m, t in out}
    phase = np.zeros(A.N, dtype=int); phase[A.B1:A.B2] = 1; phase[A.B2:A.B3] = 2; phase[A.B3:] = 3
    print(f"sim compute: {time.time()-t0:.0f}s, seeds {seeds}\n")
    for m in ["afc", "aft"]:
        res = {i: [] for i in IMPLS}
        for s in seeds:
            tl = A.build_timeline(seed=s); ts = np.array([X.focus_mm(d) for d in tl[0]])
            tg = A.afc_target_mm(tl[0], tl[5], tl[6]) if m == "afc" else ts
            for i in IMPLS:
                e = np.abs(tr[(s, i, m)] - tg)
                res[i].append([100 * np.mean(e[phase == p] < X.DEADBAND) for p in range(4)] + [100 * np.mean(e < X.DEADBAND)])
        print(f"{m.upper().replace('AF', 'AF-')}  (in-focus %, mean over seeds)   P1     P2     P3     P4   overall")
        for i in IMPLS:
            print(f"  {i:<34}" + "".join(f"{v:7.1f}" for v in np.mean(res[i], 0)))
        d = np.array(res["C float"]) - np.array(res["Python"])
        print(f"  {'C float - Python (mean ± sem)':<34}" + "".join(
            f"{a:+6.2f}" for a in d.mean(0)) + f"   (sem of overall {d[:, 4].std() / np.sqrt(len(seeds)):.2f})\n")


if __name__ == "__main__":
    main()
