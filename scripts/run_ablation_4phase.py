# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Incremental ABLATION over a 4-phase timeline, realistic servo magnetic motor.

Each config adds ONE lever on top of the previous, so the gaps show each lever's
marginal contribution; the 4th is everything combined:

  1. firmware        -- X2D today: 30 Hz loop, 4000 steps/s, time-filter (no predict)
  2. + loop rate     -- 60 Hz loop                         (4000, time-filter)
  3. + predictive    -- Kalman predict + step-clamp + coast (60 Hz, 4000)
  4. + speed cap     -- 10000 steps/s  == full X2D+        (60 Hz, 10000, predictive)
  5. AF-T            -- X2D+ plus a subject tracker (tracking AF, not AF-C)

AF-C and AF-T have DIFFERENT correct answers under occlusion, and each is
scored against its own:

  * AF-C focuses on whatever is in the AF area. When an occluder covers most of
    it (> half the zones -- where the multi-zone median flips), the correct
    target IS the occluder.
  * AF-T knows which object is the subject. It must ignore the occluder and
    stay on the subject. Modelled as an ideal subject tracker: AF zones covered
    by the occluder are excluded from the PDAF measurement; when every zone is
    covered there is no subject measurement and the policy coasts on velocity.
    (Ideal identity = an upper bound on what a real tracker achieves.)

Timeline (2.5 min):
  * P1  0-30 s    HIGH contrast, RANDOM teleports anywhere in 0.6 m .. infinity
                  (uniform in dioptres, random 1.5-3.5 s holds): step response
  * P2  30-60 s   LOW contrast 1-2% (faint texture, never fully flat)
  * P3  60-90 s   OCCLUSION: a subject moving in depth while foreground occluders
                  (textured, at their own nearer depth) cross in front of it --
                  partial (some AF zones) or full (every zone)
  * P4  90-150 s  HARSH mix: brightness + motion + fog + periodic/noise/dropout,
                  plus RAIN on random segments

Occluders and rain are not hand-coded as "no measurement": they are rendered as
their own textured layers at their own depth and composited over the subject in
the AF zones they cover, so the PDAF confidence they produce comes out of the
same optics + phase-correlation stack as everything else. A heavily defocused
near occluder blurs out (low confidence); a textured mid-depth one is a
confident measurement of the WRONG depth.

Rain model: a semi-transparent streak layer at 0.4-1.5 m that randomly hits
AF zones each frame (more zones in heavier rain), plus a haze veil (contrast
loss) and extra noise.

Metric = in-focus % (|lens - target| < deadband, target per mode as above), per phase, as mean +/- std
over N_SEEDS independent timelines (default 10; override with argv[1]).
Reuses policies / optics / motor from run_x2d_plus and the repo PDAF stack.

Run:  python scripts/run_ablation_4phase.py [n_seeds]  ->  out/ablation_4phase.png
"""
from __future__ import annotations
import importlib.util, os, sys, time
from multiprocessing import Pool
import numpy as np
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("run_x2d_plus", os.path.join(HERE, "run_x2d_plus.py"))
X = importlib.util.module_from_spec(spec); sys.modules["run_x2d_plus"] = X; spec.loader.exec_module(X)
from pdaf_sim.scene import high_contrast, H, W
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone, cdaf_score

FPS = 60
PH1, PH2, PH3, PH4 = 30 * FPS, 30 * FPS, 30 * FPS, 60 * FPS     # 30 / 30 / 30 / 60 s
B1, B2, B3 = PH1, PH1 + PH2, PH1 + PH2 + PH3                    # phase boundaries
N = B3 + PH4
F, FN, DI, PIX = X.F_MM, X.FNUM, X.DIST, X.PIX
PPM = X.PX_PER_MM; LAT = X.LATENCY; DB = X.DEADBAND; NZ = X.N_ZONES
ALIAS = X.ALIAS_PERIOD_MM
PHASES = [(0, B1, "P1: high-con random teleport 0.6 m..inf", "#e8f4ff"),
          (B1, B2, "P2: low contrast 1-2%", "#fff0f0"),
          (B2, B3, "P3: occlusion", "#f2fff0"),
          (B3, N, "P4: harsh mix + rain", "#f0f0f0")]

# config: (label, policy class in run_x2d_plus, speed cap, update_every, mode, colour)
#   mode "afc": correct target = what is in the AF area;  "aft": correct target = the subject
CFGS = [("firmware AF-C (30Hz, 4000)",  "Firmware", X.SPEED_X2D,    2, "afc", "#D85A30"),
        ("+ loop rate (60Hz)",          "Firmware", X.SPEED_X2D,    1, "afc", "#E0A030"),
        ("+ predictive (60Hz, 4000)",   "X2DPlus",  X.SPEED_X2D,    1, "afc", "#3C8DDE"),
        ("+ speed 10000 = X2D+ AF-C",   "X2DPlus",  X.SPEED_TARGET, 1, "afc", "#1D9E75"),
        ("X2D+ AF-T (subject tracker)", "X2DPlus",  X.SPEED_TARGET, 1, "aft", "#7B4FC9")]


def _zone_rows(z):
    """Row slice of AF zone z -- same split as estimate_disparity_multi_zone."""
    sh = max(1, H // NZ); lo = z * sh
    return slice(lo, lo + sh if z < NZ - 1 else H)


def rain_texture(rng):
    """Sparse thin bright streaks (falling drops) on a dark background."""
    img = np.zeros((H, W), dtype=np.float32)
    for _ in range(30):
        x = rng.integers(0, W); w = rng.integers(1, 3)
        img[:, max(0, x - w):x + w] += rng.uniform(0.4, 1.0)
    return np.clip(img, 0, 1)


def build_timeline(seed=0):
    rng = np.random.default_rng(seed)
    subj = np.zeros(N); con = np.ones(N); bright = np.ones(N); fog = np.zeros(N)
    dist = np.array([""] * N, dtype=object)
    occ = np.zeros(N); occ_d = np.zeros(N); rain = np.zeros(N)
    # ---- P1: high contrast, random teleports anywhere in 0.6 m .. infinity ----
    # Uniform in dioptres (1/m): the lens focus throw is ~linear in dioptres, so
    # this spreads targets evenly over the focus travel (0 dpt = infinity).
    k = 0
    while k < B1:
        hold = int(rng.uniform(1.5, 3.5) * FPS)
        dpt = rng.uniform(0.0, 1.0 / 0.6)
        subj[k:min(k + hold, B1)] = np.inf if dpt < 1e-3 else 1.0 / dpt
        k += hold
    # ---- P2: low contrast 1-2%, slow random-walk motion (not trivially predictable) ----
    d = 2.5; v = 0.0
    for k in range(B1, B2):
        v = 0.85 * v + float(rng.normal(0, 0.010))                     # mild momentum + noise
        d = float(np.clip(d + v, 1.0, 4.0))
        subj[k] = d
        con[k] = float(rng.uniform(0.01, 0.02))                        # 1-2% contrast
    # ---- P3: subject moving in depth + occluders crossing in front ----
    d = 3.0; v = 0.0
    for k in range(B2, B3):                                            # brisk smooth wander 1.5-6 m
        v = float(np.clip(0.97 * v + rng.normal(0, 0.0015), -0.03, 0.03))   # <= 1.8 m/s
        d += v
        if d < 1.5 or d > 6.0: d = float(np.clip(d, 1.5, 6.0)); v = -v
        subj[k] = d
    k = B2 + int(1.0 * FPS); ramp = int(0.15 * FPS)
    while k < B3:
        dur = int(rng.uniform(0.5, 1.5) * FPS)
        peak = 1.0 if rng.random() < 0.6 else float(rng.uniform(0.3, 0.7))   # full vs partial
        od = float(rng.uniform(0.5, max(0.6, subj[k] - 0.5)))            # occluder nearer than subject
        for i in range(dur):
            if k + i >= B3: break
            r = min(1.0, (i + 1) / ramp, (dur - i) / ramp)               # slide in / hold / slide out
            occ[k + i] = peak * r; occ_d[k + i] = od
        k += dur + int(rng.uniform(2.0, 4.0) * FPS)
    # ---- P4: harsh mix (run_x2d_plus generator, first PH4 frames) + rain ----
    hb, hf, hd, hs, segs = X.build_timeline(seed=seed + 1)
    subj[B3:] = hs[:PH4]; bright[B3:] = hb[:PH4]
    fog[B3:] = hf[:PH4]; dist[B3:] = hd[:PH4]; con[B3:] = 1.0
    for x0, x1, *_ in segs:
        if x0 >= PH4: break
        if rng.random() < 0.4:                                         # ~40% of segments rain
            rain[B3 + x0:B3 + min(x1, PH4)] = float(rng.uniform(0.3, 1.0))
    return subj, con, bright, fog, dist, occ, occ_d, rain


def afc_target_mm(subj, occ, occ_d):
    """AF-C truth: the occluder once it covers more than half the AF zones, else the subject."""
    t = np.array([X.focus_mm(d) for d in subj])
    m = occ > 0.5
    t[m] = [X.focus_mm(d) for d in occ_d[m]]
    return t


def measure(tex, lens, tgt_mm, con, bright, fog, dist, occ, occ_d, rain, accum, nrng, rng, aft=False):
    if dist == "dropout" and rng.random() < 0.35:
        return None, None, None
    n_occ = int(np.ceil(occ * NZ - 1e-9))                     # zones covered by the occluder
    if aft and n_occ >= NZ:                                   # tracker: subject fully hidden
        return None, None, None
    sharp, occ_tex, rain_tex = tex
    veil = (1.0 - fog) * (1.0 - 0.3 * rain)                   # fog / rain haze lowers contrast
    def flatten(img, c): return img.mean() + c * (img - img.mean())
    s = flatten(sharp, con * veil)
    noise = 0.006 + 0.030 * (1.0 - bright) + 0.030 * fog + 0.020 * rain
    occ_mm = X.focus_mm(occ_d) if n_occ else 0.0
    Ls = []; Rs = []
    for _ in range(accum):
        L, R = render_lr(s, lens - tgt_mm, F, FN, DI, PIX, noise_sigma=0.0)
        if n_occ:                                             # opaque occluder at its own depth
            Lo, Ro = render_lr(flatten(occ_tex, veil), lens - occ_mm, F, FN, DI, PIX, noise_sigma=0.0)
            for z in range(n_occ):
                rs = _zone_rows(z); L[rs] = Lo[rs]; R[rs] = Ro[rs]
        if rain > 0:                                          # semi-transparent streaks, random zones
            hit = [z for z in range(NZ) if rng.random() < 0.5 * rain]
            if hit:
                Lr, Rr = render_lr(rain_tex, lens - X.focus_mm(rng.uniform(0.4, 1.5)),
                                   F, FN, DI, PIX, noise_sigma=0.0)
                for z in hit:
                    rs = _zone_rows(z); L[rs] = 0.4 * L[rs] + 0.6 * Lr[rs]; R[rs] = 0.4 * R[rs] + 0.6 * Rr[rs]
        Ls.append(L + nrng.normal(0, noise, L.shape)); Rs.append(R + nrng.normal(0, noise, R.shape))
    L = np.mean(Ls, 0).astype(np.float32); R = np.mean(Rs, 0).astype(np.float32)
    nz = NZ
    if aft and n_occ:                                         # tracker drops the occluded zones
        r0 = _zone_rows(n_occ).start; L = L[r0:]; R = R[r0:]; nz = NZ - n_occ
    disp, c = estimate_disparity_multi_zone(L, R, max_disp_px=X.MAX_DISP_PX, n_zones=nz)
    disp_mm = disp / PPM; cs = cdaf_score((L + R) * 0.5)
    if dist == "periodic":
        disp_mm = ((disp_mm + ALIAS / 2) % ALIAS) - ALIAS / 2; c = max(c, 0.8)
    return disp_mm, float(c), float(cs)


def run(policy_cls, speed, update_every, mode, tl, seed=1):
    """Returns the lens trajectory (mm); scoring against a target happens outside."""
    subj, con, bright, fog, dist, occ, occ_d, rain = tl
    rng = np.random.default_rng(seed); nrng = np.random.default_rng(seed + 7)
    tex = (high_contrast(rng), high_contrast(rng), rain_texture(rng))
    pol = getattr(X, policy_cls)(); lb = X.LatencyBuffer(LAT)
    lens = X.focus_mm(subj[0]); last = lens; track = np.zeros(N)
    motor = X.make_motor(lens, speed)
    for k in range(N):
        tgt = X.focus_mm(subj[k])
        if k % update_every == 0:
            accum = 4 if (con[k] < 0.1 or fog[k] > 0.3 or bright[k] < 0.5) else 1
            m = measure(tex, lens, tgt, con[k], bright[k], fog[k], dist[k],
                        occ[k], occ_d[k], rain[k], accum, nrng, rng, aft=(mode == "aft"))
            arr = lb.push_pop((m[0], m[1], m[2], lens))
            last = pol.coast() if (arr is None or arr[0] is None) else pol.step(arr[0], arr[1], arr[2], arr[3])
        lens = motor.command(last); track[k] = lens
    return track


def _job(args):
    seed, ci = args
    _, cls, spd, ue, mode, _ = CFGS[ci]
    return seed, ci, run(cls, spd, ue, mode, build_timeline(seed=seed), seed=seed + 1)


def pct(e, msk=None):
    e = e if msk is None else e[msk]
    return 100 * np.mean(e < DB) if e.size else np.nan


def main():
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    os.makedirs("out", exist_ok=True)
    t0 = time.time()
    with Pool(os.cpu_count()) as pool:
        out = pool.map(_job, [(s, ci) for s in range(n_seeds) for ci in range(len(CFGS))])
    tracks = {(s, ci): tr for s, ci, tr in out}
    print(f"sim compute: {time.time()-t0:.0f}s ({N/FPS:.0f}s footage x {len(CFGS)} configs x {n_seeds} seeds)\n")

    tls = [build_timeline(seed=s) for s in range(n_seeds)]
    tgt_subj = [np.array([X.focus_mm(d) for d in tl[0]]) for tl in tls]   # AF-T truth
    tgt_afc = [afc_target_mm(tl[0], tl[5], tl[6]) for tl in tls]          # AF-C truth

    def err(s, ci, truth=None):
        truth = truth or CFGS[ci][4]
        return np.abs(tracks[(s, ci)] - (tgt_afc[s] if truth == "afc" else tgt_subj[s]))

    phase = np.zeros(N, dtype=int); phase[B1:B2] = 1; phase[B2:B3] = 2; phase[B3:] = 3
    cols = ["P1 fast", "P2 lowcon", "P3 occl", "P4 harsh", "overall",
            "P3 occ>half", "  vs subject", "  vs AF area", "P4 rain"]
    stats = {}                                                 # ci -> (n_seeds, len(cols))
    for ci in range(len(CFGS)):
        rows = []
        for s in range(n_seeds):
            e = err(s, ci); occ, rain = tls[s][5], tls[s][7]
            big = (phase == 2) & (occ > 0.5)                   # where AF-C and AF-T disagree
            rows.append([pct(e, phase == p) for p in range(4)] + [pct(e), pct(e, big),
                         pct(err(s, ci, "aft"), big), pct(err(s, ci, "afc"), big),
                         pct(e, (phase == 3) & (rain > 0))])
        stats[ci] = np.array(rows)
    print("Each config is scored against its OWN mode's truth (AF-C: what is in the AF area;"
          " AF-T: the subject).\n'vs subject' / 'vs AF area' re-score the same frames against each truth.\n")
    hdr = f"{'config (mean ± std over seeds)':<30}" + "".join(f"{c:>14}" for c in cols)
    print(hdr); print("-" * len(hdr))
    for ci, (name, *_) in enumerate(CFGS):
        m = np.nanmean(stats[ci], 0); sd = np.nanstd(stats[ci], 0)
        print(f"{name:<30}" + "".join(f"{a:>8.1f}±{b:<5.1f}" for a, b in zip(m, sd)))

    # ---- figure: seed-0 timeline + error traces, then mean ± std bars ----
    subj, con, bright, fog, dist, occ, occ_d, rain = tls[0]
    t = np.arange(N) / FPS
    fig, ax = plt.subplots(3, 1, figsize=(15, 12), gridspec_kw={"height_ratios": [1.1, 1.4, 1.4]})
    for a, b, lab, col in PHASES:
        ax[0].axvspan(a / FPS, b / FPS, color=col); ax[1].axvspan(a / FPS, b / FPS, color=col)
        ax[0].text((a + 20) / FPS, 6.6, lab, fontsize=8)

    def spans(mask):                                           # contiguous True runs
        d = np.diff(np.r_[0, mask.astype(int), 0]); return zip(np.where(d == 1)[0], np.where(d == -1)[0])
    for a, b in spans(occ > 0):
        ax[0].axvspan(a / FPS, b / FPS, color="#777", alpha=0.35, lw=0)
    for a, b in spans(rain > 0):
        ax[0].axvspan(a / FPS, b / FPS, ymin=0, ymax=0.06, color="#2a6fdb", alpha=0.6, lw=0)
    afc0 = np.where(occ > 0.5, tgt_afc[0], np.nan)
    ax[0].plot(t, tgt_subj[0], "k-", lw=0.8, label="subject focus = AF-T target (mm)")
    ax[0].plot(t, afc0, color="#c0392b", lw=1.6, label="AF-C target while occluder fills AF area")
    ax[0].plot([], [], color="#777", alpha=0.5, lw=6, label="occluder in front")
    ax[0].plot([], [], color="#2a6fdb", alpha=0.6, lw=6, label="rain")
    ax[0].set_ylabel("target (mm)"); ax[0].set_ylim(0, 7.2); ax[0].legend(fontsize=8, loc="center left")
    ax[0].set_title("4-phase timeline, seed 0 (realistic servo magnetic motor)")
    for ci, (name, *_, col) in enumerate(CFGS):
        ax[1].plot(t, err(0, ci), color=col, lw=0.7, label=name)
    ax[1].axhline(DB, color="#888", ls=":"); ax[1].set_yscale("symlog", linthresh=0.1)
    ax[1].set_ylim(bottom=0)
    ax[1].set_ylabel("defocus err (mm)"); ax[1].set_xlabel("time (s)")
    ax[1].legend(fontsize=8, ncol=3, loc="upper left")
    ax[1].set_title("Focus error vs each mode's own target, seed 0 (lower = better)")
    groups = ["P1 random\nteleport", "P2 low\ncontrast", "P3 occlusion\n(own target)",
              "P3 while occluder\nfills AF area", "P4 harsh\n+ rain", "OVERALL"]
    sel = [0, 1, 2, 5, 3, 4]                                   # columns of stats, in group order
    xg = np.arange(len(groups)); w = 0.16
    for ci, (name, *_, col) in enumerate(CFGS):
        m = np.nanmean(stats[ci][:, sel], 0); sd = np.nanstd(stats[ci][:, sel], 0)
        b = ax[2].bar(xg + (ci - 2) * w, m, w, yerr=sd, capsize=2, color=col, label=name,
                      error_kw={"lw": 0.8, "ecolor": "#444"})
        for bar, v, e in zip(b, m, sd):
            ax[2].text(bar.get_x() + bar.get_width() / 2, min(v + e, 100) + 1.5, f"{v:.0f}",
                       ha="center", fontsize=7)
    ax[2].set_xticks(xg); ax[2].set_xticklabels(groups, fontsize=9); ax[2].set_ylim(0, 112)
    ax[2].set_ylabel("in-focus %"); ax[2].legend(fontsize=8, ncol=3, loc="lower left")
    ax[2].set_title(f"In-focus % vs each mode's own target, mean ± std over {n_seeds} seeds "
                    "(AF-C: follow what fills the AF area; AF-T: stay on the subject)")
    fig.suptitle("AF-C ablation (+ loop rate + predictive + speed cap) and AF-T, over "
                 "teleport / low-contrast / occlusion / harsh+rain phases", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.98]); fig.savefig("out/ablation_4phase.png", dpi=120)
    print("\nsaved out/ablation_4phase.png")


if __name__ == "__main__":
    main()
