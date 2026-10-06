# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Equivalence tests: every C stage against its Python reference model, on inputs recorded
from the 4-phase simulator, in the double and the float build.

  1. brain   af_dual_gated.c vs pdaf_sim.policy_dual.DualGated  (every policy call replayed)
  2. eyes    af_phase.c      vs pdaf_sim.zone_tracker.zone_estimates (PDAF views, all phases)
  3. tracker af_track.c      vs pdaf_sim.zone_tracker.ZoneTracker (every select call replayed)
  4. chain   af_chain.c      vs the Python stages glued together (10 s of P3 views)

Run (after `make`):  python3 af_c/test_equiv.py
"""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import numpy as np
spec = importlib.util.spec_from_file_location("run_aft_tracker", os.path.join(ROOT, "scripts", "run_aft_tracker.py"))
T = importlib.util.module_from_spec(spec); sys.modules["run_aft_tracker"] = T; spec.loader.exec_module(T)
from pdaf_sim.policy_dual import DualGated
from pdaf_sim.zone_tracker import ZoneTracker, zone_estimates, combine_zones
from afc import CDualGated, CEyes, CTracker, CChain
A, X = T.A, T.X
NZ, PPM, W, MD = A.NZ, A.PPM, 256, X.MAX_DISP_PX
FAILED = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILED.append(msg)


class RecBrain:
    def __init__(self): self.pol = DualGated(); self.calls = []
    def step(self, d, c, cs, lens):
        out = self.pol.step(d, c, cs, lens); self.calls.append(("s", d, c, lens, out)); return out
    def coast(self):
        out = self.pol.coast(); self.calls.append(("c", 0, 0, 0, out)); return out
    def predict(self):
        out = self.pol.predict(); self.calls.append(("p", 0, 0, 0, out)); return out


class RecTracker(ZoneTracker):
    def select(self, z, c, pred):
        out = super().select(z, c, pred)
        self.calls.append((np.array(z), np.array(c), pred, None if out is None else np.array(out)))
        return out


def test_brain():
    print("1. brain (af_dual_gated.c): replay every DualGated call of AF-C and AF-T runs")
    worst = {True: 0.0, False: 0.0}; n = 0
    for seed in [0, 1]:
        for mode in ["afc", "aft"]:
            rec = RecBrain(); T.run_chain(seed, mode, brain=rec)
            calls = rec.calls; n += len(calls)
            for double in (True, False):
                pol = CDualGated(double=double)
                for kind, d, c, lens, out in calls:
                    if kind == "s": got = pol.step(d, c, 0.0, lens)
                    elif kind == "c": got = pol.coast()
                    else:
                        got = pol.predict()
                        if (got is None) != (out is None):
                            worst[double] = np.inf; continue
                        if got is None: continue
                        got, out = got[0], out[0]
                    worst[double] = max(worst[double], abs(got - out))
    print(f"     {n} calls (step / coast / predict)")
    check(worst[True] < 1e-9, f"double build max |diff| {worst[True]:.1e} mm  (< 1e-9)")
    check(worst[False] < 1e-3, f"float build  max |diff| {worst[False]:.1e} mm  (< 1e-3)")


def record_views(seed, k0, k1, every=1):
    views = []
    def eyes(L, R):
        if len(views) % every == 0:
            views.append((L.copy(), R.copy()))
        else:
            views.append(None)
        return zone_estimates(L, R, NZ, MD)
    T.run_chain(seed, "aft", eyes=eyes, k0=k0, k1=k1)
    return [v for v in views if v is not None]


def test_eyes():
    print("2. eyes (af_phase.c): per-zone disparity and confidence on simulator views, every phase")
    views = []
    for seed in [0, 1]:
        for k0, k1 in [(0, 1800), (A.B1, A.B2), (A.B2, A.B3), (A.B3, A.B3 + 1800)]:
            views += record_views(seed, k0, k1, every=12)
    ref = [zone_estimates(L, R, NZ, MD) for L, R in views]
    for double in (True, False):
        eyes = CEyes(W, NZ, MD, double=double)
        dd = []; dc = []
        for (L, R), (rd, rc) in zip(views, ref):
            cd, cc = eyes(L, R)
            dd.append(np.abs(cd - rd)); dc.append(np.abs(cc - rc))
        dd = np.concatenate(dd); dc = np.concatenate(dc)
        flips = int(np.sum(dd > 0.5))
        lab = "double" if double else "float "
        print(f"     {lab}: {len(views)} frames x {NZ} zones: disparity max |diff| {np.max(dd[dd <= 0.5]) if np.any(dd <= 0.5) else 0:.1e} px, "
              f"confidence max |diff| {dc.max():.1e}, peak flips {flips}")
        check(np.percentile(dd, 99.9) < (1e-4 if double else 1e-2), f"{lab} disparity 99.9th pct |diff| {np.percentile(dd, 99.9):.1e} px")
        check(flips <= 0.001 * dd.size, f"{lab} correlation-peak flips {flips} of {dd.size} zone estimates (<= 0.1%)")


def test_tracker():
    print("3. tracker (af_track.c): replay every ZoneTracker decision of AF-T runs")
    calls = []
    for seed in [0, 1]:
        tr = RecTracker(); tr.calls = []
        T.run_chain(seed, "aft", tracker=tr)
        calls.append(tr.calls)
    for double in (True, False):
        mism = 0; n = 0
        for seq in calls:
            ct = CTracker(double=double)
            for z, c, pred, out in seq:
                got = ct.select(z, c, pred); n += 1
                if (got is None) != (out is None) or (got is not None and not np.array_equal(got, out)):
                    mism += 1
        lab = "double" if double else "float "
        check(mism == 0 if double else mism <= 0.001 * n, f"{lab}: {mism} of {n} decisions differ")


def test_chain():
    print("4. chain (af_chain.c): eyes -> tracker -> combine -> brain, on 10 s of recorded P3 views")
    frames = record_views(0, A.B2, A.B2 + 600)
    for double in (True, False):
        ch = CChain("aft", W, A.H, NZ, MD, PPM, double=double)
        pb, pt = DualGated(), ZoneTracker()                     # the same stages, in Python
        lens = X.focus_mm(A.build_timeline(seed=0)[0][A.B2]); diff = []
        for L, R in frames:
            d, c = zone_estimates(L, R, NZ, MD)
            sel = pt.select(lens - d / PPM, c, pb.predict())
            if sel is None:
                p = pb.coast()
            else:
                use = sel if np.any(sel) else np.ones(NZ, dtype=bool)
                dd, cc = combine_zones(d[use], c[use]); p = pb.step(dd / PPM, cc, 0.0, lens)
            diff.append(abs(ch.frame(L, R, lens) - p))
            lens = p                                            # both see the same next exposure
        diff = np.array(diff); lab = "double" if double else "float "
        check(np.mean(diff < 1e-3) >= (0.999 if double else 0.99),
              f"{lab}: {len(frames)} frames, commands within 1e-3 mm on {100*np.mean(diff < 1e-3):.1f}% "
              f"(median |diff| {np.median(diff):.1e} mm)")


if __name__ == "__main__":
    test_brain(); test_eyes(); test_tracker(); test_chain()
    print(f"\n{'FAIL' if FAILED else 'PASS'}: {len(FAILED)} failed check(s)")
    sys.exit(1 if FAILED else 0)
