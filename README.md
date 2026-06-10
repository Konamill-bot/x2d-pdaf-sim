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
report mean ± std. The simulation includes lens motor rate limiting
(no teleporting lens) and a fixed scene per trial (measurement errors
are *not* i.i.d. across frames, so the temporal prior is not given an
unrealistic advantage).

Numbers, tables and the full argument live in
[TECHNICAL_PROPOSAL.md](TECHNICAL_PROPOSAL.md).

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
- `scripts/run_latency_sweep.py` — sensitivity of config D to
  uncompensated ISP latency
- `scripts/run_v4_tracking.py` — moving subject + occlusion demo
- `scripts/fit_policy.py` — behavioural-cloning fit of policy
  parameters to observed lens trajectories (differential evolution)
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
