# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Does the BIG lever (AF loop rate) lift the MIXED / harsh-environment test?

The mixed 2-minute stress test (run_x2d_plus) was run at a handicapped 30 Hz AF
loop. The loop-rate study (run_big_levers) showed rate is the dominant lever.
So here we re-run the SAME randomly-combined harsh timeline (brightness + motion
+ fog + periodic/noise/dropout) at 30 Hz vs 60 Hz, for both the baseline and the
predictive policy, and compare in-focus %.

Reuses the mixed-environment timeline and policies from run_x2d_plus.

Run:  python scripts/run_stress_fps.py   ->  out/stress_fps.png
"""
from __future__ import annotations
import importlib.util, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("run_x2d_plus", os.path.join(HERE, "run_x2d_plus.py"))
X = importlib.util.module_from_spec(spec)
sys.modules["run_x2d_plus"] = X          # register before exec (dataclass needs it)
spec.loader.exec_module(X)

DB = X.DEADBAND


def main():
    os.makedirs("out", exist_ok=True)
    bright, fog, distort, subj, segs = X.build_timeline(seed=0)
    mot = np.array([""] * X.N, dtype=object); dst = np.array([""] * X.N, dtype=object)
    for x0, x1, br, fg, dist, m in segs:
        mot[x0:x1] = m; dst[x0:x1] = dist

    configs = [(2, "30Hz (every 2f)"), (1, "60Hz (every 1f)")]
    out = {}
    for ue, lab in configs:
        X.UPDATE_EVERY = ue                               # the big lever
        e_fw = X.run(X.Firmware(), bright, fog, distort, subj)
        e_pl = X.run(X.X2DPlus(), bright, fog, distort, subj)
        out[lab] = (e_fw, e_pl)
        print(f"\n== {lab} ==")
        print(f"  firmware overall in-focus: {100*np.mean(e_fw<DB):5.1f}%")
        print(f"  X2D+    overall in-focus: {100*np.mean(e_pl<DB):5.1f}%")

    # overall bars: firmware vs X2D+ at 30 vs 60 Hz
    labels = ["firmware\n30Hz", "firmware\n60Hz", "X2D+\n30Hz", "X2D+\n60Hz"]
    vals = [100*np.mean(out["30Hz (every 2f)"][0] < DB),
            100*np.mean(out["60Hz (every 1f)"][0] < DB),
            100*np.mean(out["30Hz (every 2f)"][1] < DB),
            100*np.mean(out["60Hz (every 1f)"][1] < DB)]
    cols = ["#D85A30", "#993C1D", "#1D9E75", "#0F6E56"]
    fig, ax = plt.subplots(1, 2, figsize=(14, 5.5))
    b = ax[0].bar(labels, vals, color=cols, width=0.6)
    for bar, v in zip(b, vals): ax[0].text(bar.get_x()+bar.get_width()/2, v+1, f"{v:.0f}%", ha="center", fontsize=10)
    ax[0].set_ylabel("in-focus %  (whole 2-min harsh timeline)"); ax[0].set_ylim(0, 100)
    ax[0].set_title("Raising the AF loop rate lifts the harsh-environment result")

    # per-condition, 60Hz, firmware vs X2D+
    conds = ["steady", "erratic", "step"]; dconds = ["periodic", "noise", "dropout"]
    allc = conds + dconds
    fw60, pl60 = out["60Hz (every 1f)"]
    def pct(e, msk): return 100*np.mean(e[msk] < DB) if msk.sum() else 0
    fwv = [pct(fw60, mot==c) for c in conds] + [pct(fw60, dst==c) for c in dconds]
    plv = [pct(pl60, mot==c) for c in conds] + [pct(pl60, dst==c) for c in dconds]
    x = np.arange(len(allc)); w = 0.38
    ax[1].bar(x-w/2, fwv, w, color="#993C1D", label="firmware @60Hz")
    ax[1].bar(x+w/2, plv, w, color="#0F6E56", label="X2D+ @60Hz")
    ax[1].set_xticks(x); ax[1].set_xticklabels(allc, fontsize=8)
    ax[1].set_ylabel("in-focus %"); ax[1].set_ylim(0, 100)
    ax[1].set_title("Per-condition at 60Hz (firmware vs X2D+)"); ax[1].legend(fontsize=8)
    fig.suptitle("Harsh 2-min stress test: raise the AF loop rate (30->60Hz) + predictive policy", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96]); fig.savefig("out/stress_fps.png", dpi=120)
    print("\nsaved out/stress_fps.png")


if __name__ == "__main__":
    main()
