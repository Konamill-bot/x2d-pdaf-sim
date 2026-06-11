# Development Log

> *Internal development log preserved for transparency. Captures
> intermediate hypotheses, failed configurations, and reasoning
> along the way. Final claims live in [`README.md`](README.md)
> and [`FINDINGS.md`](FINDINGS.md); this file documents how the
> work got there.*

---


## What I built

A v2 policy (`CompositePolicy` in `pdaf_sim/policy.py`) plus a multi-zone
confidence metric (`estimate_disparity_multi_zone` in
`pdaf_sim/phase_corr.py`) plus a new benchmark T6 specifically targeting
the near-focus PSR-drop failure mode.

The v2 policy implements three improvements over Temporal v1:

1. **Multi-zone agreement confidence** — split AF strip into 3 zones,
   take median disparity, multiply base confidence by inter-zone
   agreement (`exp(-spread/2)`).
2. **Near-focus bonus** — when median disparity is near zero AND zones
   agree, add a confidence bonus so the policy does NOT misinterpret
   the in-focus broad-peak as low confidence.
3. **Stickiness** — once locked (3 high-conf frames in a row), tolerate
   up to 8 low-conf frames before sweeping. This prevents a single
   noise spike from triggering hunt.

## Results — honest, not flattering

See `out/v2_comparison.png` and the table below.

| Benchmark              | Stateless | Temporal v1 | Composite v2 |
|------------------------|-----------|-------------|--------------|
| T1 low-contrast static | 120 / 0%  | **44 / 21%**| 87 / 0%      |
| T2 post-shutter reset  | OK        | OK          | OK           |
| T3 step response       | bad       | bad         | bad          |
| T4 repeating pattern   | 120       | 120         | **59**       |
| T5 breathing           | 120 / 0%  | 120 / 0%    | **59 / 18%** |
| T6 near focus          | OK        | OK          | OK           |

Columns are: sweep count / in-focus fraction.

## Findings

1. **v2 is NOT strictly better than v1.** It wins on false-signal
   scenarios (T4 repeating, T5 breathing) but loses on truly featureless
   scenes (T1). Reason: when every zone sees only noise, multi-zone
   median is still noise — the composite confidence is correctly low,
   but the policy spends time in CDAF sweep without locking.

2. **The two failure classes require different fixes:**
   - **False signal** (peak in correlation but unreliable): multi-zone
     agreement helps — different zones disagree on false peaks,
     so composite confidence drops as it should.
   - **Truly absent signal** (no peak at all, or only noise): no amount
     of clever confidence helps. The policy must either accept failure
     (timeout with indicator, like X2D's red box) or escalate to
     hardware assistance (LiDAR on X2D II, AF illuminator pattern).

3. **T6 (near-focus) passes for all three policies in simulation, but
   this is a sim limitation, not a fix.** Real X2D fails on this case.
   Tomorrow: collect real disparity-vs-defocus measurement near focus
   on the X2D and calibrate our PSR confidence model to actually
   reproduce the drop. Until then we can't claim to have fixed T6.

4. **T3 (step response) is bad for all three.** Subject motion across
   wide defocus ranges (5mm -> 0.5mm jumps) exceeds our policies'
   adaptation bandwidth. Two possible directions:
   - Detect jump scenarios (sudden large disparity) and reset Kalman
     covariance to allow rapid re-locking.
   - Add subject-velocity prior (IMM filter with stationary +
     constant-velocity + jump models).
   This is real research territory — not a 1-night fix.

## What's actually validated for the Hasselblad letter

Despite the mixed v2 results, the v1 (Temporal) policy delivers a clean
finding that's safe to cite:

> "Adding a Kalman temporal prior with confidence-weighted measurement
> variance reduces hunting sweeps in low-contrast scenarios from 120 to
> 44 (63% reduction) and raises in-focus time from 0% to 21%."

That alone is a defensible quantitative improvement from a single
firmware change, on simulated data calibrated against XCD 55V optics.

## v3 finding (added later same session) — AF-readout binning

User suggestion: bin the sensor readout from 100MP to 25MP or 6.25MP
during the AF half-press phase, freeing ISP/bandwidth and raising AF
framerate.

Added `bin_factor` parameter to `render_lr` and ran
`scripts/run_binning_sweep.py` with Temporal v1 on the T1 low-contrast
scene. Results:

| bin | effective fps | sweeps | time-to-focus | result        |
|-----|---------------|--------|---------------|---------------|
| 1x1 | 15            | 30     | never         | total failure |
| 2x2 | 30            | 26     | 1.37 s        | marginal      |
| 4x4 | 60            | **0**  | **0.18 s**    | clean lock    |

**Interpretation**: the Kalman temporal prior needs sufficient sample
density to converge. At 15 fps the noise averaging is too slow; at 60 fps
the filter accumulates 12 measurements within 200 ms and locks cleanly.
This is the single largest leverage we've found tonight, and it costs
no algorithm change at all -- it's a sensor mode register switch that
every BSI CMOS sensor including the X2D's supports natively. Sony,
Canon, Nikon all do this during AF half-press.

Output: `out/binning_sweep.png`.

The three independent firmware levers identified tonight, in order of
likely impact:

1. **AF processing at the framerate the binned readout already supplies
   for EVF** (sensor binning is not a new capability to request — it is
   already running for the 5.76 M-dot EVF; the ask is to use it for AF):
   eliminates hunting entirely in sim
2. **Temporal prior on PDAF confidence**: 63% sweep reduction
3. **Multi-zone agreement metric**: false-peak / repeating-pattern fixes

All three are firmware-only, zero-hardware. The first is essentially
free engineering effort.

## Day 2 — v3 stack (deadband + PID + PDAF/CDAF fusion)

After Sonnet/Opus review tightened the lever-1 framing to a conditional
claim, the user (via Sonnet) suggested adding four more techniques to
the policy stack: deadband control, PID lens drive, spatial PDAF
gradient, and object-detection-style subject persistence.

First two implemented as `V3Policy` plus a CDAF score function. Last
two deferred to v4 -- they require 2-D zone layout and a subject-motion
model that the current 1-D strip abstraction doesn't accommodate
without restructuring.

`scripts/run_v3_comparison.py` runs Stateless / v1 / v3 head-to-head
at 60 fps with 4x4 binning (since lever 1 is a precondition for any
temporal-prior policy to function).

| Metric                | low_c v1 | low_c v3 | high_c v1 | high_c v3 |
|-----------------------|----------|----------|-----------|-----------|
| sweeps                | 1        | 0        | 0         | 0         |
| time to lock (s)      | 0.22     | 0.33     | 0.18      | 0.15      |
| final error (mm)      | 0.30     | 0.22     | 0.20      | 0.26      |
| lens travel (mm/2s)   | 2.7      | **1.9**  | 1.8       | 1.7       |

Honest reading: v3 is not strictly better than v1. It buys 30% less
lens motor travel (motor longevity) and eliminates the final sweep
event entirely, in exchange for ~100 ms slower lock on low-contrast
subjects. The deadband + PID combination prioritises mechanical
quietness and stability over absolute speed -- the right trade for
a still-photography body.

Engineering note on the CDAF fusion: an early version of V3Policy used
`0.5 * sign(cdaf_gradient)` as the fusion nudge, which produced random
lens wander when the CDAF gradient itself was noise (in low-contrast
scenes the gradient magnitude is comparable to its noise). Adding a
`cdaf_grad_min` threshold below which CDAF input is ignored, and
shrinking the per-frame nudge from 0.5 mm to 0.1 mm, fixed it. This
is exactly the kind of failure mode any "fusion" algorithm has to
handle and is worth documenting because it suggests the same care
applies in a production firmware: CDAF should never override PDAF
when CDAF itself is below its own confidence floor.

Output: `out/v3_comparison.png`.

## Day 2 — v4 stack: 2D zone grid + bounding-box tracker

A new `pdaf_sim/scene2d.py` adds a 2-D scene (192x256) with a moving
textured subject on a low-contrast background, optional occlusion.
`pdaf_sim/policy2d.py::V4Policy` reads a grid of 15 PDAF zones
(5 columns x 3 rows), maintains a 4-D Kalman state over subject
(x, y, dx, dy) in addition to the existing focus Kalman, and
continues driving the lens from the predicted subject position when
all zones report low confidence (occlusion).

`scripts/run_v4_tracking.py` runs a 3-second scenario: a textured
subject crosses the frame left-to-right at constant velocity, with
a 0.33 s occlusion mid-traverse.

Comparison vs v1 (1-D Kalman over the centre zone only):

| Metric                              | v1     | v4    |
|-------------------------------------|--------|-------|
| in-focus % (err < 0.3 mm)           | 2.8    | 64.4  |
| std of lens position after settle   | 4.08   | 0.22  |
| max focus error                     | 24.5   | 1.0   |
| catastrophic events (err > 1 mm)    | 169    | 0     |
| mean error during occlusion         | 11.71  | 0.30  |

Caveat to read this honestly: v1 was never designed for a moving
subject (TemporalPolicy assumes the AF zone consistently sees the
subject). When the subject leaves the centre zone, v1's Kalman starts
absorbing background noise as if it were measurement data, and the
estimate drifts unboundedly because I did not put a saturation on the
commanded position. A real camera firmware would bound this. So the
"v1 vs v4" gap shown is partially exaggerated by v1's missing safety
net -- but the architectural point stands: v1 only sees one zone, v4
sees the whole frame, and that gap is real regardless of saturation.

Output: `out/v4_tracking.png` (the green line at 2.5mm is v4; the
blue line wandering to 25mm is v1).

Determinism fix: `dualpixel.render_lr` now accepts an `rng` argument
so noise is reproducible. Without this, plots changed between runs.

## Deferred to v5

- **Spatial PDAF gradient** across nearby zones for motion direction
  prediction. Requires 2-D zone layout in `pdaf_sim` (currently a
  single 1-D strip).
- **Subject-motion model / object detection persistence**. A bounding
  box predictor that survives brief occlusions and is robust to AF
  area changes. Requires the simulator to have a "subject" object
  with size and trajectory, not just a focus distance.

Both are well-scoped 1-2 day additions individually, blocked on
having the 2-D zone layout in place. That refactor is the natural
next step when work resumes.

## Open work for tomorrow

- **Calibrate PSR confidence model with real X2D data** (so T6 actually
  reproduces the in-focus failure)
- **Test E** (Auto Area + off-centre target) to validate L2
- **Test F** (cover-uncover repeatability check on the AF assist lamp)
- `git init` + push to GitHub
- Draft Letter 2 framing using FINDINGS.md + this file

## Files added/modified tonight

- `pdaf_sim/phase_corr.py` — added `estimate_disparity_multi_zone`
- `pdaf_sim/policy.py` — added `CompositePolicy`
- `pdaf_sim/benchmarks.py` — added `bench_near_focus_psr_drop`
- `scripts/run_v2_comparison.py` — new
- `out/v2_comparison.png` — new
- `DEV_LOG.md` — this file

---

## Day 3 — v3 experiment: model-validity review and five fixes

A fresh-eyes review of the v2 IMX461 experiment found two
reproduce-level bugs and three fidelity gaps. All five fixed; in the
process the fixes exposed a sixth, deeper bug that had been present
since day 1. Chronicle below, because the sequence is instructive.

### The five planned fixes

1. **A/B were the same experiment.** Configs A and B shared identical
   parameters; nothing in the code modelled the framerate difference.
   The letter's "binning alone quadruples wasted travel" numbers came
   from the older single-strip sim only. Fixed: per-config fps
   (A at 15 fps = 30 frames over the 2 s window; binned configs at
   60 fps = 120 frames).

2. **Scene regenerated every frame.** Measurement errors were i.i.d.
   across frames — an unrealistically favourable setting for the
   temporal prior, whose whole job is averaging independent noise.
   Fixed: each zone's sharp patch is generated once per trial;
   only photon noise is fresh per frame. Systematic per-scene bias
   now persists, as in reality.

3. **No motor dynamics.** The lens teleported to the commanded
   position. Fixed: per-frame rate limit of 18 mm/s focus-group
   travel (0.3 mm/frame at 60 fps).

4. **Strawman baseline.** The stateless fallback alternated direction
   every frame (+/-0.2 mm jitter) and could never reach a target
   2.5 mm away — the real X2D usually locks or red-boxes within
   1-2 s. Fixed: monotonic bounded scan that reverses only at travel
   limits. The same alternating-sweep flaw was then found and fixed
   in TemporalPolicy and V3Policy.

5. **Latency compensation documented but not implemented.** New
   config F = D + 2-frame ISP latency with timestamp-correct
   measurement association (measurement fused against the lens
   position AT CAPTURE, not current). This is the fix the letter's
   caveat (b) describes; it converts the naive-latency catastrophe
   (0 % in-focus) into a modest duty-cycle cost.

### The bug the fixes exposed (day-1 vintage)

With a fixed scene and motor dynamics, high-contrast configs suddenly
parked at exactly +0.34 mm error, every seed. Tracing the disparity
response curve revealed **the rendered disparity never changed sign**:
`render_lr` used the absolute CoC radius with fixed L/R half-disk
kernels, so front-focus and back-focus produced identical (negative)
disparity. Every previous experiment had survived this because the
lens always approached from one side; any overshoot was pushed
*further away*, masked by per-frame scene regeneration.

Fixing the sign exposed a second rendering artifact: below ~1 px CoC
radius the discrete half-disk kernels quantize to identical deltas,
creating an artificial ±0.34 mm measurement dead zone — wider than
the 0.3 mm in-focus criterion. Real masked-pixel PDAF resolves
sub-pixel disparity.

Both fixed by replacing the half-disk convolution with full-disk blur
plus Fourier sub-pixel shift of ±(4r/3π) — the half-disk centroid
offset, which is the actual PDAF displacement signal, now exact at
any magnitude including deep sub-pixel.

### v3 results (30-seed sanity; 1000-seed run in progress)

| config | low_contrast | high_contrast |
|---|---|---|
| A baseline (15 fps scan)        | 8.8 ± 4.8  | 90.7 ± 7.2 |
| B + binned readout (60 fps)     | 9.3 ± 0.5  | 93.6 ± 0.7 |
| C + Kalman                      | 90.8 ± 5.2 | 94.2 ± 0.0 |
| D + multi-zone (N=5)            | 92.1 ± 0.6 | 94.2 ± 0.0 |
| E + V3 stack                    | 91.9 ± 1.3 | 92.7 ± 0.4 |
| F = D + 2f latency, compensated | 77.0 ± 1.6 | 92.5 ± 0.0 |

How the story changed — and why the new one is stronger:

- **The fair baseline now matches the real X2D on BOTH sides**: high
  contrast mostly locks (90.7 %, with ±7.2 variance — the stochastic
  lock-vs-hunt behaviour observed on the real camera), low contrast
  mostly fails (8.8 %) — which is exactly the user-reported pain
  pattern. The earlier "baseline never locks at all" was an artifact
  of the strawman sweep.
- **Binning alone still doesn't help** (8.8 → 9.3): finding 1 of the
  proposal survives, with honest numbers.
- **The Kalman prior remains the hero lever** (8.8 → 90.8 on low
  contrast).
- **Multi-zone's value is now correctly identified as variance
  reduction** (±5.2 → ±0.6), not the 9× mean lift the v2 sim
  suggested — that lift was an artifact of the v2 aggregation
  formula's confidence veto, which has been softened (agreement now
  modulates confidence with a 0.5× floor rather than vetoing
  acquisition).
- **V3 is rehabilitated**: its v2 "failure" (1 ± 9 %) was caused by
  the measurement artifacts, not the concept. With honest
  measurements it performs on par with D (91.9 / 92.7).
- **Latency caveat is now a solved demonstration**: compensated
  2-frame latency costs ~15 points of low-contrast duty cycle and
  nothing on high contrast. Correction to the v2-era claim: under
  the v3 model the motor rate limit damps the naive-latency
  oscillation, so uncompensated 2-frame latency is severe
  degradation (41/32 %) rather than the total failure (0 %) seen
  in v2. The ranking (compensated >> naive) is unchanged; the
  rewritten run_latency_sweep.py now shows both curves directly.

TECHNICAL_PROPOSAL.md numbers to be re-synced from the 1000-seed run.

## Day 4 — AF-C feasibility (run_afc.py v1 -> v2)

Question: can the same decision stack do continuous AF (AF-C) on this
sensor geometry? Hasselblad's position is that the X2D cannot meet
their AF-C standard. The TemporalPolicy Kalman state was always
(position, VELOCITY) — the velocity term is the core of predictive
AF-C — so the test is pointing the existing stack at a moving subject.

### v1 — and the two ways it was wrong

First version: constant-velocity walk-in, all four configs scored
100 %. Honest reading: a smooth mover is trivially easy; one frame of
lag at 60 fps is 0.02 mm, deep inside the 0.3 mm band. The experiment
didn't discriminate anything.

Second version added intermittent confidence dropout — and got the
dramatic result (AF-S hunts 36x, AF-C zero) **by construction**: the
dropout confidence (0.15) was hand-placed between AF-S's trust
threshold (0.35) and AF-C's sweep floor (0.08). A reviewer would
correctly call the magnitude an artifact of threshold placement.

### v2 hardening (four fixes, mirrors the Day-3 discipline)

- **H1 dropout grid**: sweep P(dropout) x conf floor across BOTH
  boundaries instead of one chosen point. Result: the AF-C advantage
  lives only in the middle band (conf between the two thresholds),
  grows with dropout rate (+11 pp at P=0.3 -> +40 pp at P=0.5), and
  vanishes at both edges exactly as the mechanism predicts (conf
  above 0.35: invisible to both; conf below 0.08: both sweep, AF-C
  advantage -1 pp i.e. noise). The boundary map IS the claim now —
  the mechanism is real, and its domain is explicit.
- **H2 erratic motion**: approach / hard stop / reverse — two velocity
  discontinuities, the constant-velocity prior's worst case. Result:
  AF-C coasts past the stop by only 0.10 mm worst-case (0.17 mm with
  look-ahead), recovering within a few frames because 60 fps
  measurements re-pin the velocity state quickly. No CA (acceleration)
  state needed at walking speeds. AF-S on the same scenario: worst
  error 0.83 mm, 36 hunts.
- **H3 pixel-level focus band**: scored against the defocus where CoC
  radius = 1 native pixel (3.76 um) alongside the 0.3 mm band.
  Surprise worth recording: the 1-px-radius band computes to 0.494 mm
  — LOOSER than the 0.3 mm band we'd been using, which corresponds to
  ~0.6 px CoC. Our original criterion was already at-or-tighter than
  pixel-level. (If one insists on 1 px *diameter*, the band is
  ~0.25 mm, comparable to 0.3.) Either way AF-C scores 100 % on both.
- **H4 burst blackout**: periodic PDAF-blind windows at ~3.3 fps burst
  cadence (7 of every 18 frames blind). The stateless policy can only
  hold through a gap; the Kalman coasts on velocity (new
  `TemporalPolicy.coast()`). Result: AF-C bridges every gap (100 %,
  lag 0.025 mm). Honest footnote: blackouts actually *help* AF-S
  slightly (92.2 % vs 89.6 %) because a blind frame can't trigger a
  hunt — holding is safer than hunting, which is itself an indictment
  of the hunt-on-dropout policy. Latency+blackout interaction costs
  the look-ahead config some lag (0.091 mm, worst 0.24 mm) but stays
  in-band.

### What this does and does not show

Shows: the decision-algorithm half of AF-C is not the blocker on this
geometry — velocity prediction was already in the AF-S stack, costs
microseconds per frame, and survives dropout, hard stops, and burst
blackouts in simulation.

Does not show: that the X2D ISP sustains a 60 fps AF loop (same
"hardware ceiling" caveat as FINDINGS.md), nor anything about the
LATERAL half of AF-C (subject recognition, zone hand-off as the
subject moves across the frame) — the depth-tracking question was
deliberately isolated by keeping the subject centred. The lateral
half is the part the X2D II ships a deep-learning model for, and
remains out of scope here (FINDINGS.md Layer 3 note applies).

Files: scripts/run_afc.py (new), pdaf_sim/policy.py
(TemporalPolicy.predict_frames + coast()), out/afc_tracking.png,
out/afc_dropout_grid.png, out/afc_burst.png.

### H5 (v3, added after firmware-evidence check) — AF-loop-rate sweep

The 60 fps AF loop was the study's largest unverifiable assumption, so
v3 stopped assuming it. What is externally checkable:

- X2D firmware 3.1.0 (2023-11-30) added face detection in AF mode —
  proof that some continuous per-frame computation runs on the live
  stream (Hasselblad release notes). The detector's own inference
  rate is NOT published anywhere we could find.
- The EVF stream is 60 fps (published spec), so the sensor side
  sustains a 60 Hz subsampled stream.
- On mobile-class ISPs, detectors commonly run at 1/2 or 1/4 of the
  stream rate (15–30 Hz) with interpolation between inferences.

So v3 sweeps the AF measurement loop at 15 / 30 / 60 fps, with motor
step, latency (33 ms wall time -> frames), grace, and burst cadence
all scaled by rate, and subject motion held in real units (mm/s).

Results (walk / erratic, 30 % dropout, AF-C carries 33 ms latency):

| rate | AF-S walk | AF-C walk | AF-S erratic | AF-C erratic |
|---|---|---|---|---|
| 15 fps | 98.9 % (8.8 hunts) | 99.9 % (0) | 87.4 % (8.8) | **95.3 % (0)** |
| 30 fps | 95.6 % (17.8) | 100 % (0) | 92.6 % (17.8) | 100 % (0) |
| 60 fps | 89.5 % (35.8) | 100 % (0) | 87.1 % (35.9) | 100 % (0) |

Two honest surprises, both worth keeping:

1. **AF-S gets BETTER as the loop slows** (89.5 -> 98.9 on walk).
   Hunting is a per-measurement pathology: fewer measurements per
   second = fewer dropout-triggered sweeps, and the larger per-frame
   motor budget recovers each excursion faster. The corollary cuts
   the other way for Hasselblad: if the X2D's loop is slow, that
   does not explain the hunting away — a slow stateless loop hunts
   LESS in this model. The visible hunting is the threshold policy,
   not the rate.
2. **AF-C's first sub-100 cell**: erratic subject at 15 fps = 95.3 %,
   worst error 0.32 mm — the velocity prior coasts past the hard
   stop and, with only ~30 measurements in the window, takes longer
   to re-pin. This is the genuine boundary of the CV-Kalman at
   detector subrates, recorded rather than hidden.

Net for the claim: the AF-C conclusion no longer depends on the
60 fps assumption. At the MOST conservative reading of the firmware
evidence (15 fps detector-style subrate), velocity-prior AF-C tracks
a walking subject at ~100 % and an erratic one at 95 %, with ZERO
hunts at every rate — and hunts are the user-visible failure.

Files: scripts/run_afc.py (v3, fps threaded through; latency now in
wall-time ms), out/afc_fps_sweep.png (new).
