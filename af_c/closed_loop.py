# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Closed loop with every AF stage in C (float build: what would run on a target) against the
Python reference stages, through the full 4-phase simulator, AF-C and AF-T.

The simulator (scene, optics, motor, injected aliasing) stays in Python; the C eyes, tracker
and brain do all of the AF work. Closed loop, a 1e-6 mm difference can eventually flip one
discrete decision and the trajectories fork, so compare the means, not single seeds. The C
double build is the control: it is the same code as the float build, and any spread it shows
against Python is that forking, not precision.

Run (after `make`):  python3 af_c/closed_loop.py [n_seeds]
"""
import importlib.util, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
from multiprocessing import Pool
import numpy as np
spec = importlib.util.spec_from_file_location("run_aft_tracker", os.path.join(ROOT, "scripts", "run_aft_tracker.py"))
T = importlib.util.module_from_spec(spec); sys.modules["run_aft_tracker"] = T; spec.loader.exec_module(T)
from afc import CDualGated, CEyes, CTracker
A, X = T.A, T.X


IMPLS = ["Python", "C float", "C double"]    # C double = control: same code, double precision


def _job(a):
    s, impl, mode = a
    if impl == "Python":
        tr = T.run_chain(s, mode)
    else:
        dbl = impl == "C double"
        tr = T.run_chain(s, mode, brain=CDualGated(double=dbl), tracker=CTracker(double=dbl) if mode == "aft" else None,
                         eyes=CEyes(256, A.NZ, X.MAX_DISP_PX, double=dbl))
    return s, impl, mode, tr


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
            for i in res:
                e = np.abs(tr[(s, i, m)] - tg)
                res[i].append([100 * np.mean(e[phase == p] < X.DEADBAND) for p in range(4)] + [100 * np.mean(e < X.DEADBAND)])
        print(f"{'AF-C' if m == 'afc' else 'AF-T'}  (in-focus %, mean over seeds)       P1     P2     P3     P4   overall")
        for i in res:
            print(f"  {'all stages ' + i:<38}" + "".join(f"{v:7.1f}" for v in np.mean(res[i], 0)))
        for i in ("C float", "C double"):
            d = np.array(res[i]) - np.array(res["Python"])
            print(f"  {i + ' - Python (mean ± sem)':<38}" + "".join(f"{v:+7.2f}" for v in d.mean(0))
                  + "   sem " + " ".join(f"{v:.2f}" for v in d.std(0) / np.sqrt(len(seeds))))
        print()


if __name__ == "__main__":
    main()
