# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Incremental ABLATION over a 3-phase timeline, realistic servo magnetic motor.

Each config adds ONE lever on top of the previous, so the gaps show each lever's
marginal contribution; the 4th is everything combined:

  1. firmware        -- X2D today: 30 Hz loop, 4000 steps/s, time-filter (no predict)
  2. + loop rate     -- 60 Hz loop                         (4000, time-filter)
  3. + predictive    -- Kalman predict + step-clamp + coast (60 Hz, 4000)
  4. + speed cap     -- 10000 steps/s  == full X2D+        (60 Hz, 10000, predictive)

Timeline (2 min):
  * 0-30 s   HIGH contrast, FAST teleports 3m -> 8m -> 20m (repeat): step response
  * 30-60 s  LOW contrast 1-2% (faint texture, never fully flat): measurement-limited
  * 60-120 s HARSH mix: brightness + motion + fog + periodic/noise/dropout (random)

Metric = in-focus % and defocus error, broken down per phase.
Reuses policies / optics / motor from run_x2d_plus and the repo PDAF stack.

Run:  python scripts/run_ablation_3phase.py   ->  out/ablation_3phase.png
"""
from __future__ import annotations
import importlib.util, os, sys, time
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_x2d_plus", os.path.join(HERE, "run_x2d_plus.py"))
X = importlib.util.module_from_spec(spec); sys.modules["run_x2d_plus"] = X; spec.loader.exec_module(X)
from pdaf_sim.scene import high_contrast
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone, cdaf_score

FPS = 60
PH1, PH2, PH3 = 30 * FPS, 30 * FPS, 60 * FPS          # 30s / 30s / 60s
N = PH1 + PH2 + PH3
F, FN, DI, PIX = X.F_MM, X.FNUM, X.DIST, X.PIX
PPM = X.PX_PER_MM; LAT = X.LATENCY; DB = X.DEADBAND; LO, HI = X.LO, X.HI
ALIAS = X.ALIAS_PERIOD_MM
PHASES = [(0, PH1, "P1: high-con fast teleport 3/8/20m", "#e8f4ff"),
          (PH1, PH1 + PH2, "P2: low contrast 1-2%", "#fff0f0"),
          (PH1 + PH2, N, "P3: harsh mix", "#f0f0f0")]


def build_timeline(seed=0):
    rng = np.random.default_rng(seed)
    subj = np.zeros(N); con = np.ones(N); bright = np.ones(N)
    fog = np.zeros(N); dist = np.array([""] * N, dtype=object)
    # ---- P1: high contrast, fast teleports between 3 / 8 / 20 m ----
    tele = [3.0, 8.0, 20.0]; hold = int(2.5 * FPS)
    for k in range(PH1):
        subj[k] = tele[(k // hold) % len(tele)]; con[k] = 1.0
    # ---- P2: low contrast 1-2%, slow random-walk motion (not trivially predictable) ----
    d = 2.5; v = 0.0
    for k in range(PH1, PH1 + PH2):
        v = 0.85 * v + float(rng.normal(0, 0.010))                     # mild momentum + noise
        d = float(np.clip(d + v, 1.0, 4.0))
        subj[k] = d
        con[k] = float(rng.uniform(0.01, 0.02))                        # 1-2% contrast
    # ---- P3: harsh mix (reuse the run_x2d_plus harsh generator, first PH3 frames) ----
    hb, hf, hd, hs, _ = X.build_timeline(seed=seed + 1)
    subj[PH1 + PH2:] = hs[:PH3]; bright[PH1 + PH2:] = hb[:PH3]
    fog[PH1 + PH2:] = hf[:PH3]; dist[PH1 + PH2:] = hd[:PH3]; con[PH1 + PH2:] = 1.0
    return subj, con, bright, fog, dist


def measure(sharp, lens, tgt_mm, con, bright, fog, dist, accum, nrng, rng):
    if dist == "dropout" and rng.random() < 0.35:
        return None, None, None
    eff = con * (1.0 - fog)                                   # contrast after fog veil
    s = sharp.mean() + eff * (sharp - sharp.mean())
    noise = 0.006 + 0.030 * (1.0 - bright) + 0.030 * fog
    Ls = []; Rs = []
    for _ in range(accum):
        L, R = render_lr(s, lens - tgt_mm, F, FN, DI, PIX, noise_sigma=noise, rng=nrng)
        Ls.append(L); Rs.append(R)
    L = np.mean(Ls, 0); R = np.mean(Rs, 0)
    disp, c = estimate_disparity_multi_zone(L, R, max_disp_px=X.MAX_DISP_PX, n_zones=X.N_ZONES)
    disp_mm = disp / PPM; cs = cdaf_score((L + R) * 0.5)
    if dist == "periodic":
        disp_mm = ((disp_mm + ALIAS / 2) % ALIAS) - ALIAS / 2; c = max(c, 0.8)
    return disp_mm, float(c), float(cs)


def run(make_policy, speed, update_every, subj, con, bright, fog, dist, seed=1):
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed + 7)
    sharp = high_contrast(rng); pol = make_policy(); lb = X.LatencyBuffer(LAT)
    lens = X.focus_mm(subj[0]); last = lens; err = np.zeros(N)
    motor = X.make_motor(lens, speed)
    for k in range(N):
        tgt = X.focus_mm(subj[k])
        if k % update_every == 0:
            accum = 4 if (con[k] < 0.1 or fog[k] > 0.3 or bright[k] < 0.5) else 1
            m = measure(sharp, lens, tgt, con[k], bright[k], fog[k], dist[k], accum, nrng, rng)
            arr = lb.push_pop((m[0], m[1], m[2], lens))
            last = pol.coast() if (arr is None or arr[0] is None) else pol.step(arr[0], arr[1], arr[2], arr[3])
        lens = motor.command(last); err[k] = abs(lens - tgt)
    return err


def main():
    os.makedirs("out", exist_ok=True)
    subj, con, bright, fog, dist = build_timeline(seed=0)
    cfgs = [("firmware (30Hz, 4000)",        lambda: X.Firmware(), X.SPEED_X2D,    2, "#D85A30"),
            ("+ loop rate (60Hz)",           lambda: X.Firmware(), X.SPEED_X2D,    1, "#E0A030"),
            ("+ predictive (60Hz, 4000)",    lambda: X.X2DPlus(),  X.SPEED_X2D,    1, "#3C8DDE"),
            ("+ speed 10000 = X2D+ (all)",   lambda: X.X2DPlus(),  X.SPEED_TARGET, 1, "#1D9E75")]
    t = np.arange(N) / FPS; res = {}
    t0 = time.time()
    for name, mk, spd, ue, col in cfgs:
        e = run(mk, spd, ue, subj, con, bright, fog, dist); res[name] = (e, col)
    print(f"sim compute: {time.time()-t0:.1f}s ({N/FPS:.0f}s footage x {len(cfgs)} configs)\n")

    def pct(e, a, b): return 100 * np.mean(e[a:b] < DB)
    hdr = f"{'config':<30}{'P1 fast':>9}{'P2 lowcon':>11}{'P3 harsh':>10}{'overall':>9}"
    print(hdr); print("-" * len(hdr))
    for name, (e, _) in res.items():
        print(f"{name:<30}{pct(e,0,PH1):>8.1f}%{pct(e,PH1,PH1+PH2):>10.1f}%"
              f"{pct(e,PH1+PH2,N):>9.1f}%{100*np.mean(e<DB):>8.1f}%")

    fig, ax = plt.subplots(3, 1, figsize=(15, 11),
                           gridspec_kw={"height_ratios": [1.1, 1.4, 1.3]})
    # row 0: target focus + phase shading
    for a, b, lab, col in PHASES:
        ax[0].axvspan(a / FPS, b / FPS, color=col); ax[1].axvspan(a / FPS, b / FPS, color=col)
        ax[0].text((a + 20) / FPS, 6.5, lab, fontsize=8)
    ax[0].plot(t, [X.focus_mm(d) for d in subj], "k-", lw=0.8, label="target focus (mm)")
    ax[0].set_ylabel("target (mm)"); ax[0].set_ylim(0, 7.2); ax[0].legend(fontsize=8, loc="lower left")
    ax[0].set_title("3-phase timeline (realistic servo magnetic motor)")
    # row 1: defocus error per config
    for name, (e, col) in res.items(): ax[1].plot(t, e, color=col, lw=0.7, label=name)
    ax[1].axhline(DB, color="#888", ls=":"); ax[1].set_yscale("symlog", linthresh=0.1)
    ax[1].set_ylabel("defocus err (mm)"); ax[1].set_xlabel("time (s)")
    ax[1].legend(fontsize=8, ncol=2, loc="upper left"); ax[1].set_title("Focus error (lower = better)")
    # row 2: grouped bars, per phase + overall
    groups = ["P1 fast\nteleport", "P2 low\ncontrast", "P3 harsh\nmix", "OVERALL"]
    xg = np.arange(len(groups)); w = 0.2
    for i, (name, (e, col)) in enumerate(res.items()):
        vals = [pct(e, 0, PH1), pct(e, PH1, PH1 + PH2), pct(e, PH1 + PH2, N), 100 * np.mean(e < DB)]
        b = ax[2].bar(xg + (i - 1.5) * w, vals, w, color=col, label=name)
        for bar, v in zip(b, vals):
            ax[2].text(bar.get_x() + bar.get_width() / 2, v + 1, f"{v:.0f}", ha="center", fontsize=7)
    ax[2].set_xticks(xg); ax[2].set_xticklabels(groups); ax[2].set_ylim(0, 100)
    ax[2].set_ylabel("in-focus %"); ax[2].legend(fontsize=8, ncol=2)
    ax[2].set_title("In-focus % by phase (each config adds one lever; last = all combined)")
    fig.suptitle("AF-C ablation: + loop rate + predictive + speed cap, over fast / low-contrast / harsh phases",
                 fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.98]); fig.savefig("out/ablation_3phase.png", dpi=120)
    print("\nsaved out/ablation_3phase.png")


if __name__ == "__main__":
    main()
