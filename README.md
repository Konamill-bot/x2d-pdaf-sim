# x2d-pdaf-sim

An open simulation testbench for phase-detection autofocus (PDAF)
decision policies on mirrorless cameras, with the Hasselblad X2D 100C
(firmware 4.2.0) as the running case study.

The headline question:

> Given identical PDAF hardware, how much of an AF body's hunting
> behaviour is determined by the firmware decision policy that consumes
> the PDAF output?

## Headline result

![lever-by-lever comparison](out/imx461_full_stack_metrics.png)

The experiment simulates the X2D's actual sensor geometry — Sony
IMX461, 294 PDAF zones (21 × 14) tiled across the active area, each
zone phase-correlated at the native 3.76 µm pitch — and stacks
firmware-level changes one lever at a time:

- **A. baseline** — stateless single-frame PSR threshold, 15 fps AF
  loop, single zone (a fair monotonic-scan CDAF fallback, not a
  strawman)
- **B. + binned AF readout** — same stateless policy at 60 fps
- **C. + Kalman temporal prior** — confidence-weighted measurement
  fusion across frames
- **D. + multi-zone aggregation** — nearest-5 zones,
  confidence-weighted, agreement-damped
- **E. + deadband / PID / CDAF fusion** — mechanical-smoothness stack
  (currently mistuned for multi-zone inputs; kept for transparency)
- **F. = D + 2-frame ISP latency with timestamp-correct measurement
  association** — demonstrates that pipeline latency is handled by
  standard predictive-AF bookkeeping, not a blocker

Run on 1000 independent seeds per configuration per scene; figures
report mean ± std. The lens is modelled as a servo-controlled magnetic
voice-coil motor (critically damped, velocity-capped at the speed
setting, acceleration-limited, no gear backlash — no teleporting lens),
and the scene is fixed per trial (measurement errors are *not* i.i.d.
across frames, so the temporal prior is not given an unrealistic
advantage). The PDAF/CDAF fusion is **confidence-gated**, not a fixed
weight ratio: PDAF leads, CDAF verifies near focus and backs off under
low confidence.

Numbers, tables and the full argument live in
[TECHNICAL_PROPOSAL.md](TECHNICAL_PROPOSAL.md).

## Continuous AF (AF-C): the same stack tracks a moving subject

The AF-S study above asks "can the lens converge on a stationary
subject without hunting?" The follow-up asks the harder question —
the one usually answered with "the hardware can't": can the **same
decision stack** track a subject that is *moving* in depth?

It can, because the Kalman state already contains a velocity term —
and a velocity estimate *is* the core of predictive continuous AF.
No new machinery was added; the existing AF-S stack was pointed at
moving subjects.

![AF-C tracking](out/afc_tracking.png)

A walking subject (constant velocity) and an erratic one (approach,
hard stop, reverse — the worst case for a velocity prior), both with
30 % intermittent confidence dropout, the regime where the stateless
policy hunts:

| 60 fps loop | track % (±0.3 mm) | worst error | hunts / 2 s |
|---|---|---|---|
| stateless (AF-S policy) | 87–90 % | 0.65–0.83 mm | **~36** |
| Kalman velocity prior (AF-C) | **100 %** | 0.05–0.10 mm | **0** |
| AF-C + 33 ms pipeline latency | **100 %** | 0.05–0.17 mm | **0** |

Three hardening passes matter as much as the headline (all documented
in `DEV_LOG.md`, including the flawed first version):

- **Dropout grid, not a chosen point** — the AF-C advantage is mapped
  across the full dropout-probability × confidence plane, *including
  the regions where it vanishes* (`out/afc_dropout_grid.png`). The
  mechanism's predicted boundaries match the measured ones.
- **Burst blackout** — periodic PDAF-blind windows at a ~3.3 fps burst
  cadence. The stateless policy can only hold through a gap; the
  Kalman coasts on velocity and bridges every one
  (`out/afc_burst.png`).
- **The AF-loop-rate assumption is removed entirely:**

![AF-C loop-rate sweep](out/afc_fps_sweep.png)

The only externally verifiable facts are that the EVF stream runs at
60 fps and that firmware 3.1.0 added face detection — proof of *some*
continuous per-frame computation, at an unpublished rate. So instead
of assuming 60 fps, the experiment sweeps the AF loop at 15 / 30 /
60 fps. The AF-C conclusion survives at every rate — at the most
conservative reading (15 fps detector-style subrate), it still tracks
a walking subject at ~100 % and an erratic one at 95 %, with zero
hunts. Two honest surprises are recorded in the dev log: the
stateless policy actually hunts *less* at slower loop rates (hunting
is a per-measurement pathology — so a slow loop cannot explain the
hunting away), and the erratic/15 fps cell is the genuine boundary of
the constant-velocity prior (95.3 %, worst error 0.32 mm).

**Scope, honestly:** this demonstrates the *depth-tracking* half of
AF-C. The lateral half — subject recognition and zone hand-off as the
subject moves across the frame — needs a subject-detection model and
is deliberately out of scope (that is legitimately X2D II territory).
And whether the X2D's ISP scheduler grants the AF loop a guaranteed
time slot is only measurable inside the camera. What the simulation
rules out is the *algorithm* and the *arithmetic*: an ISP budget
estimate in [FINDINGS.md](FINDINGS.md) shows the AF-C decision stack
adds ~2 MB/s and microseconds of compute per frame to a camera that
already streams ~480 MB/s to the EVF and runs face detection.

## Extended AF-C algorithm studies

Six additional simulation studies isolating *where* AF-C quality actually comes
from (simulation only — see [DISCLAIMER.md](DISCLAIMER.md)).

**Loop rate is the big lever; speed cap only helps big throws.** Sweeping AF loop
rate × lens speed cap, with a *servo-controlled magnetic voice-coil* lens model
(critically damped, velocity-capped, acceleration-limited, no backlash — an
XCD 55V-style linear motor). Speed caps are modelled at the real values:
4000 steps/s (X2D today), 10000 (proposed), 12000 (X2D II), 24000 (silicon
ceiling). For *normal* subject motion the loop rate dominates (7.5 → 15 → 30
→ 60 Hz: 64% → 81% → 92% → 99%) and the four speed caps essentially overlap — a
faster motor adds almost nothing once it already outruns the subject. Speed only
pays off on a *big rack focus* (0.6 m ↔ 8 m): settle time 4000 = 450 ms →
10000 = 239 ms → 12000 = 228 ms → 24000 = 183 ms. The temporal filter is a minor
lever (+6 at 30 Hz).

The sweep now also runs a **90 Hz** point: the sensor-side ceiling. The IMX461's
fastest 12-bit readout mode at a usable resolution is 90.5 fps (Sony flyer,
readout mode 12, 3884 × 970, about 4 MP). 120 Hz, as in the GFX100 II's Boost
mode, is a newer sensor; the IMX461-generation GFX100S boosts to 85.7 fps,
consistent with that ceiling. 90 Hz adds only +0.3 over 60 Hz here: for normal
motion, **60 Hz already reaches the ceiling**, and the big step is X2D's
30 Hz → 60 Hz (+7). (The simulation clock moved to 180 Hz so all rates land on
whole ticks, which shifts the earlier numbers by a few ms / points.)

![AF loop rate vs speed cap vs filter](out/big_levers.png)

**Why AF-C stays laggy — timing.** Pipeline latency + loop rate vs a predictive
filter (a non-predictive smoother lags; prediction tuned to the latency recovers it):

![latency and loop-rate sweep](out/realism_sweep.png)

**Adaptive policy in realistic units (subject distance in metres), high vs low contrast:**

![adaptive policy, high vs low contrast](out/adaptive_v2.png)

**Periodic patterns fool phase-detect (confident false lock); CDAF disambiguates:**

![periodic-pattern aliasing](out/periodic.png)

**Timing a front → background focus pull (prediction overshoots on sudden steps):**

![focus-pull timing](out/focus_pull.png)

**Low contrast is measurement-limited — temporal integration recovers faint
texture without LiDAR; a textureless target is unfocusable by any passive method:**

![low-contrast temporal integration](out/lowcontrast_fix.png)

**Mixed / harsh-environment stress test (~2 minutes).** Brightness, motion type,
fog, and distortions (periodic aliasing / noise bursts / occlusion dropouts)
randomly combined per segment; an improved policy vs the baseline, with a
per-condition in-focus breakdown. Firmware (30 Hz, 4000) vs X2D+ (60 Hz, 10000):
56% → 68% overall. X2D+ wins where its mechanisms apply — erratic motion
(28 → 48%), periodic aliasing (38 → 52%), occlusion dropouts (42 → 66%) — and
ties where the baseline is already fine (static / steady / step / noise, all
≥ 95%). Single seed; see the multi-seed ablation below for error bars:

![mixed harsh-environment stress test](out/x2d_plus.png)

**Core AF-C job, normal conditions — moving subject + occlusion.** A brisk
walk-in with ~1 s occlusion dropouts, good light and contrast. Firmware runs on
X2D's current hardware (30 Hz, 4000 steps/s); the full proposal is 60 Hz at
10000 steps/s. The baseline freezes and *loses the focus zone* during occlusion
(≈45% in-focus there, 64% overall); a predictive filter on the *same* hardware
coasts through it (≈82% / 90%), and the full proposal holds the subject
(≈87% through occlusion, 94% overall):

![moving subject + dropout: firmware vs Kalman vs X2D+](out/moving_dropout.png)

**Does raising the loop rate lift the *harsh* mix?** Yes. Re-running the
2-minute harsh timeline at 30 Hz vs 60 Hz: firmware 56% → 62%, X2D+ 51% → 68%.
The predictive policy *needs* the faster loop. At 30 Hz it is actually below
the baseline on this seed, because its velocity estimate is too stale to
extrapolate on. (An earlier version of this paragraph said the harsh mix was
"measurement-limited" and loop rate barely helped. That was an artefact of a
timeline bug that teleported the subject every frame; see `DEV_LOG.md`,
"Harsh-timeline teleport bug".)

![harsh timeline at 30 vs 60 Hz](out/stress_fps.png)

**Incremental ablation over a 4-phase timeline.** Adding one lever at a time
(loop rate → predictive policy → speed cap) over 2.5 minutes, with the realistic
servo magnetic motor:

- **P1 (30 s)**: high-contrast *random teleports* anywhere from 0.6 m to
  infinity (uniform in dioptres, random 1.5–3.5 s holds)
- **P2 (30 s)**: *1–2% contrast*
- **P3 (30 s)**: *occlusion*. The subject wanders in depth while textured
  foreground occluders cross in front of it, sometimes covering only some
  AF zones and sometimes all of them
- **P4 (60 s)**: the *harsh mix* plus *rain* on random segments

Occluders and rain are rendered as their own textured layers at their own
depth and composited into the AF zones they cover. Their PDAF confidence
comes out of the same optics + phase-correlation stack; nothing is
hand-coded as "no measurement".

**AF-C and AF-T have different correct answers under occlusion**, so each mode
is scored against its own:

- **AF-C** focuses on whatever is in the AF area. When an occluder fills most
  of it (more than half the zones, which is where the multi-zone median flips),
  focusing on the occluder *is* the right answer.
- **AF-T** knows which object is the subject. It should ignore the occluder
  and stay on the subject. It is modelled as X2D+ plus an ideal subject
  tracker: occluded AF zones are dropped from the PDAF measurement, and when
  every zone is covered the policy coasts on its velocity estimate. Ideal
  identity makes this an upper bound on what a real tracker achieves.

Mean ± std over 10 independent timelines, each config scored against its
own mode's target:

| config | P1 teleport | P2 low-con | P3 occlusion | P3 while occluder fills AF area | P4 harsh+rain | overall |
|---|---|---|---|---|---|---|
| firmware AF-C (30 Hz, 4000) | 88 ± 1% | 65 ± 15% | 85 ± 4% | 63 ± 8% | 68 ± 13% | 75 ± 7% |
| + loop rate (60 Hz) | 93 ± 1% | 78 ± 12% | 91 ± 3% | 77 ± 7% | 72 ± 12% | 81 ± 6% |
| + predictive (60 Hz, 4000) | 93 ± 1% | 88 ± 9% | 91 ± 3% | 78 ± 7% | 79 ± 9% | 86 ± 4% |
| + speed 10000 (= X2D+ AF-C) | 95 ± 1% | 88 ± 9% | 93 ± 2% | 84 ± 5% | 75 ± 12% | 85 ± 6% |
| **X2D+ AF-T** (subject tracker) | 95 ± 1% | 88 ± 9% | **99 ± 1%** | **95 ± 6%** | 76 ± 12% | **87 ± 6%** |

What each lever does:

- **Loop rate** is the broadest lever. It lifts every phase, and it is the
  biggest single gain for AF-C switching onto an occluder (63 → 77%).
- **Predictive** carries low contrast (+10) and the harsh mix (+7).
- **Speed cap** pays off wherever focus has to travel far and fast: the big
  random teleports (+2) and AF-C snapping onto a near occluder (78 → 84%).
  Everywhere else it is within noise.

AF-C and AF-T behave as each should. While an occluder fills the AF area, the
AF-C configs sit on the subject only 9–19% of the time, because they correctly
move to the occluder. AF-T sits on the occluder only 3% of the time and
holds the subject 95% of the time. Outside occlusion AF-T matches X2D+ AF-C,
so subject tracking costs nothing elsewhere. Rain frames alone: 73% → 78%
(firmware → X2D+).

![4-phase ablation](out/ablation_4phase.png)

## What's in this repo

- `pdaf_sim/psf.py` — circle-of-confusion radius, half-disk sub-aperture
  PSFs, signed disparity prediction
- `pdaf_sim/dualpixel.py` — synthesize left/right PDAF views from a sharp
  image, with optional sensor binning and reproducible noise
- `pdaf_sim/phase_corr.py` — 1-D phase correlation with sub-pixel
  refinement, PSR confidence, multi-zone agreement, CDAF score
- `pdaf_sim/policy.py` — Stateless / Temporal (Kalman) / V3
  (deadband + PID + CDAF fusion) / Behavioral (fittable) policies
- `pdaf_sim/policy2d.py` — V4: 2-D zone grid + subject bounding-box
  Kalman tracker (subject persistence across occlusion)
- `pdaf_sim/sensor_imx461.py` — IMX461 geometry: 294-zone grid,
  native PDAF pitch, zone selection helpers
- `pdaf_sim/latency.py` — ISP pipeline-latency buffer
- `pdaf_sim/benchmarks.py` — standardized AF benchmarks with
  `REAL_ANCHORS` (direct observations of X2D 100C and Sony A7 IV)
- `scripts/run_imx461_stats.py` — **the headline experiment** (1000
  seeds, lever-by-lever, IMX461 geometry)
- `scripts/run_afc.py` — **the AF-C study**: moving-subject depth
  tracking, dropout grid, burst blackout, AF-loop-rate sweep
- `scripts/run_latency_sweep.py` — sensitivity of config D to
  uncompensated ISP latency
- `scripts/run_v4_tracking.py` — moving subject + occlusion demo
- `scripts/fit_policy.py` — behavioural-cloning fit of policy
  parameters to observed lens trajectories (differential evolution)

Extended AF-C algorithm studies (simulation-only — see `DISCLAIMER.md`):

- `scripts/run_big_levers.py` — **AF loop RATE × lens SPEED CAP sweep** (7.5–90 Hz,
  90 Hz = IMX461 readout ceiling) with a servo magnetic voice-coil motor, speed
  caps at 4000/10000/12000/24000 steps/s.
  Loop rate dominates normal motion; speed cap only cuts big rack-focus settle
  time; the temporal filter is minor. Config is the big lever, not the algorithm.
- `scripts/run_realism_sweep.py` — pipeline **latency** + loop-rate vs a
  predictive filter: a non-predictive smoother lags under latency; prediction
  tuned to the latency recovers it.
- `scripts/run_adaptive_v2.py` — an **adaptive** policy (step-clamp +
  confidence-scaled prediction) in realistic units (subject distance in metres);
  high vs low contrast.
- `scripts/run_periodic.py` — **periodic-pattern aliasing**: phase detection can
  lock confidently on a *false* plane; a CDAF-coarse + PDAF-fine hybrid
  disambiguates where pure PDAF cannot.
- `scripts/run_focus_pull.py` — **timing a front→background pull** under
  latency / loop-rate / motor lag plus a foreground distractor; shows prediction
  overshoots on a sudden step (so it is motion-dependent).
- `scripts/run_lowcontrast_fix.py` — low contrast is **measurement-limited**:
  temporal integration recovers faint texture *without LiDAR*, while a truly
  textureless target is unfocusable by any passive method (physics).
- `scripts/run_x2d_plus.py` — **mixed / harsh-environment stress test (~2 min)**:
  brightness, motion, fog, and distortions (periodic / noise / dropout) randomly
  combined; improved policy vs baseline with a per-condition breakdown.
- `scripts/run_moving_dropout.py` — **core AF-C in normal conditions**: a brisk
  moving subject + ~1 s occlusions; firmware vs Kalman vs X2D+ (60 Hz + bias fix).
  The baseline loses the focus zone during occlusion; the proposal holds it.
- `scripts/run_stress_fps.py` — re-runs the harsh 2-min timeline at 30 Hz vs
  60 Hz: the loop rate lifts the harsh mix, and the predictive policy needs it.
- `scripts/run_ablation_4phase.py` — **incremental ablation** (+ loop rate +
  predictive + speed cap) plus an **AF-T** config, over a 4-phase timeline
  (random 0.6 m–∞ teleports / 1-2% contrast / occlusion / harsh mix + rain),
  realistic servo magnetic motor, 10 seeds. AF-C is scored on following what
  fills the AF area, AF-T on staying on the subject.
- `FINDINGS.md` — direct behavioural observations of the X2D 100C,
  organized by causal layer, cross-referenced with reviews and patents
- `DEV_LOG.md` — development log capturing intermediate hypotheses,
  failed configurations, and reasoning along the way
- `TECHNICAL_PROPOSAL.md` — the technical proposal this study supports,
  also intended for delivery as personal correspondence to Hasselblad

Earlier iterations (single-strip optics, superseded figures) are
preserved under `scripts/archive/` and `out/archive/` — the DEV_LOG
explains why each was superseded.

## Run

```bash
pip install -r requirements.txt
python scripts/run_imx461_stats.py     # headline figure (multi-core, ~2 min)
python scripts/run_afc.py              # AF-C study, all four figures (~1.5 min)
python scripts/run_latency_sweep.py    # latency sensitivity figure
```

## Case study constants

X2D-100C-class: Sony IMX461 (43.8 × 32.9 mm, 11656 × 8750, 3.76 µm),
294 PDAF zones, XCD 2,5/55V at f/2.5, 1.5 m subject. Swap the
constants in `pdaf_sim/sensor_imx461.py` to model another body.

## Real-camera anchors

The simulation is anchored against direct observations of:

- Hasselblad X2D 100C (firmware 4.2.0) with XCD 2,5/55V
- Sony A7 IV with 50mm f/1.8

Observed hunt times, failure indicators and behaviour signatures live
in `pdaf_sim/benchmarks.py::REAL_ANCHORS` and `FINDINGS.md`. The
baseline's stochastic same-scene behaviour in simulation independently
reproduces the stochastic lock-vs-hunt behaviour observed on the real
X2D — the one place where the synthetic model and the physical camera
cross-validate.

## What this is NOT

- Not a claim about Hasselblad's internal firmware implementation.
  Decision policies are generic and based on public PDAF literature;
  X2D constants are used only to make disparity magnitudes realistic.
  The firmware is closed: observed behaviour is consistent both with
  these techniques being absent and with them being present but
  limited elsewhere.
- Not a reverse-engineered firmware patch. The X2D firmware is signed
  and is not modified here. This is a behavioural / decision-policy
  study built entirely from public information and direct external
  observation.
- Not a deep-learning method. A linear Kalman prior is the minimum
  thing that should outperform a stateless threshold; if it does,
  more sophisticated approaches become worth exploring.

## Citations and further reading

- USPTO 9910247 — Focus hunting prevention for phase detection autofocus
- USPTO 9729779 — Phase detection autofocus noise reduction
- USPTO 11523071 — Disparity-preserving binning for phase detection
  autofocus (evidence that naive binning destroys PDAF phase signal)
- *Improving the Reliability of Phase Detection Autofocus* (IS&T)
- Sony Semiconductor IMX461 product flyer (binning modes documented
  for moving-picture output; PDAF-in-binned-mode behaviour not public)
- Capture Integration — *Fujifilm GFX 100S II — Profoundly Better
  Autofocus* (algorithmic-only PDAF improvement across two generations
  with shared hardware)

## License

MIT.

## Acknowledgements

Built in conversation with Claude (Anthropic) as a thinking partner,
in the spirit of "a smart colleague who walks into the room." Final
technical claims and observations are the author's responsibility.

## Disclaimer

Personal research / independent investigation only. **Simulation code only — no
firmware, no `.cim` files, no decrypted or extracted firmware data** is included
or required. This is an idealised model of autofocus *algorithms*, not a
reverse-engineering of any product's implementation. Not affiliated with or
endorsed by Hasselblad / DJI / any manufacturer. Provided "as is", without
warranty. See [DISCLAIMER.md](DISCLAIMER.md) for the full text.
