# Tonight v2 — summary written while you slept

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

## Deferred to v4

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
- `TONIGHT_v2.md` — this file
