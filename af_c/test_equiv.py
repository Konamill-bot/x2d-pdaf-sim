# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Equivalence test: Python reference (pdaf_sim.policy_dual.DualGated) vs the C port.

1. Run the Python reference closed-loop through the full 4-phase simulator (AF-C and
   AF-T, three seeds) and record every policy call: its inputs and its output.
2. Replay exactly those inputs, open-loop, into the C double and float builds and compare.

Pass criterion: the double build matches the reference to < 1e-9 mm on every call.
Run (after `make`):  python3 af_c/test_equiv.py
"""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import numpy as np
spec = importlib.util.spec_from_file_location("run_ablation_4phase", os.path.join(ROOT, "scripts", "run_ablation_4phase.py"))
A = importlib.util.module_from_spec(spec); sys.modules["run_ablation_4phase"] = A; spec.loader.exec_module(A)
from pdaf_sim.policy_dual import DualGated
from afdg import CDualGated
X = A.X


class Recorder:
    def __init__(self): self.pol = DualGated(); self.calls = []
    def step(self, d, c, cs, lens):
        out = self.pol.step(d, c, cs, lens); self.calls.append((0, d, c, lens, out)); return out
    def coast(self):
        out = self.pol.coast(); self.calls.append((1, 0.0, 0.0, 0.0, out)); return out


def record(seed, mode):
    rec = Recorder()
    X.RecordedDualGated = lambda: rec              # A.run resolves the policy by name on X
    A.run("RecordedDualGated", X.SPEED_TARGET, 1, mode, A.build_timeline(seed=seed), seed=seed + 1)
    return rec.calls


def replay(calls, double):
    pol = CDualGated(double=double); out = np.zeros(len(calls))
    for i, (kind, d, c, lens, _) in enumerate(calls):
        out[i] = pol.coast() if kind == 1 else pol.step(d, c, 0.0, lens)
    return out


def main():
    worst_d = worst_f = 0.0; n = flips = 0
    for seed in [0, 1, 2]:
        for mode in ["afc", "aft"]:
            calls = record(seed, mode); ref = np.array([c[4] for c in calls]); n += len(calls)
            dd = np.abs(replay(calls, True) - ref); df = np.abs(replay(calls, False) - ref)
            worst_d = max(worst_d, dd.max()); worst_f = max(worst_f, df.max()); flips += int(np.sum(df > 1e-3))
            print(f"seed {seed} {mode}: {len(calls)} calls ({sum(c[0] for c in calls)} coast) | "
                  f"double max|diff| {dd.max():.1e} mm | float max|diff| {df.max():.1e} mm")
    print(f"\nTOTAL {n} calls: double max|diff| = {worst_d:.1e} mm; float max|diff| = {worst_f:.1e} mm "
          f"({flips} calls differ by > 1e-3 mm)")
    assert worst_d < 1e-9, "C double build diverges from the Python reference"
    print("PASS: C (double) matches the Python reference")


if __name__ == "__main__":
    main()
