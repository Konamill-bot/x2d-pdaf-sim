# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""C vs Python for subject-box AF: af_roi.c (the ISP window rules, the ROI-led depth veto),
af_pc_window and af_chain's AF_MODE_ROI, on data from scripts/run_subject_classes.py with the
baseline ISP rules of scripts/run_isp_window.py.

  1. ISP window : af_isp_fit vs pdaf_sim.roi.IspWindow.fit on 20,000 random boxes and rules
  2. eyes       : af_pc_window vs estimate_disparity on every ISP window of 4 recorded clips
  3. veto+brain : the closed loop with Python eyes and the C chain (double build) vs Python,
                  every lens command of 8 clips
  4. closed loop: C eyes + C chain (float build) vs Python, in-focus %, 4 classes x 5 seeds

Run (after make):  python3 af_c/test_roi.py
"""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import numpy as np
spec = importlib.util.spec_from_file_location("run_subject_classes", os.path.join(ROOT, "scripts", "run_subject_classes.py"))
S = importlib.util.module_from_spec(spec); sys.modules["run_subject_classes"] = S; spec.loader.exec_module(S)
from pdaf_sim.roi import IspWindow
from afc import CWindowEyes, CChain, c_isp_fit
ISP = dict(win=IspWindow(), lat=1, quant=True)
FAILED = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILED.append(msg)


def test_isp():
    print("1. ISP window (af_isp_fit) vs IspWindow.fit, random boxes and rules")
    rng = np.random.default_rng(0); bad = 0
    for _ in range(20000):
        y0, x0 = int(rng.integers(-8, 64)), int(rng.integers(-40, 520))
        box = (y0, y0 + int(rng.integers(1, 40)), x0, x0 + int(rng.integers(1, 200)))
        rule = (int(rng.choice([1, 4, 8, 16])), int(rng.integers(8, 96)), int(rng.integers(2, 24)))
        want = tuple(int(v) for v in IspWindow(*rule).fit(*box, S.W, S.H))
        bad += want != c_isp_fit(box, S.W, S.H, *rule)
    check(bad == 0, f"{bad} of 20000 windows differ")


def test_eyes():
    print("2. eyes (af_pc_window) vs estimate_disparity, every ISP window of 4 clips")
    crops = []
    def rec(L, R, w):
        a = np.ascontiguousarray(L[w[0]:w[1], w[2]:w[3]]); b = np.ascontiguousarray(R[w[0]:w[1], w[2]:w[3]])
        out = S.estimate_disparity(a, b, max_disp_px=S.MD_CELL); crops.append((a, b, out)); return out
    for cls in S.CLASSES:
        S.run(0, cls, 4, isp=ISP, impl=dict(eyes=rec))
    for double in (True, False):
        eyes = CWindowEyes(S.MD_CELL, double=double); dd = []; dc = []
        for a, b, (rd, rc) in crops:
            got = eyes(a, b, (0, a.shape[0], 0, a.shape[1]))
            dd.append(abs(got[0] - rd)); dc.append(abs(got[1] - rc))
        dd = np.array(dd); dc = np.array(dc); flips = int(np.sum(dd > 0.5)); lab = "double" if double else "float "
        print(f"     {lab}: {len(crops)} windows: disparity 99.9th pct |diff| {np.percentile(dd, 99.9):.1e} px, "
              f"confidence max |diff| {dc.max():.1e}, peak flips {flips}")
        check(np.percentile(dd, 99.9) < (1e-4 if double else 1e-2) and flips <= 0.001 * len(dd),
              f"{lab} window estimates match")


def test_chain():
    print("3. veto + brain (af_chain AF_MODE_ROI, double build) in the closed loop, Python eyes")
    worst = 0.0; n = 0
    for cls in S.CLASSES:
        for seed in (0, 1):
            py = S.run(seed, cls, 4, isp=ISP)
            ch = CChain("roi", S.W, S.H, 1, S.MD_CELL, S.PPM, double=True)
            c = S.run(seed, cls, 4, isp=ISP, impl=dict(chain=ch))
            worst = max(worst, float(np.max(np.abs(py - c)))); n += len(py)
    check(worst < 1e-9, f"{n} lens commands, max |diff| {worst:.1e} mm (< 1e-9)")


def test_closed_loop():
    print("4. closed loop: everything after the views in C (float build) vs Python, baseline ISP")
    ok = True
    for cls in S.CLASSES:
        d = []
        for seed in range(5):
            py = S.score(seed, cls, S.run(seed, cls, 4, isp=ISP))[0]
            ch = CChain("roi", S.W, S.H, 1, S.MD_CELL, S.PPM)
            c = S.score(seed, cls, S.run(seed, cls, 4, isp=ISP, impl=dict(eyes=CWindowEyes(S.MD_CELL), chain=ch)))[0]
            d.append(c - py)
        m, se = float(np.mean(d)), float(np.std(d) / np.sqrt(len(d)))
        print(f"     {cls:<16} in focus: C float - Python {m:+.2f} ± {se:.2f} points (5 seeds)")
        ok &= abs(m) < 3.0
    check(ok, "C float within 3 in-focus points of Python for every class")


if __name__ == "__main__":
    test_isp(); test_eyes(); test_chain(); test_closed_loop()
    print(f"\n{'FAIL' if FAILED else 'PASS'}: {len(FAILED)} failed check(s)")
    sys.exit(1 if FAILED else 0)
