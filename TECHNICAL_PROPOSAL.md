# Technical Proposal — X2D 100C Autofocus Behavioural Study

> *This document is the technical proposal that this study supports.
> A version of the text below is intended to be delivered as personal
> correspondence to Hasselblad's product team. The public version
> exists so the methodology, simulation results, and recommendations
> can be cited and reviewed independently of any communication outcome.
> The text is unchanged between the two forms; only the surrounding
> framing differs.*

---

Subject: Follow-up to AF-C inquiry — X2D 100C autofocus behavioural study

Dear Hasselblad Product Team,

Thank you for the prompt acknowledgement of my earlier letter regarding
AF-C on the X2D 100C. While that review is in progress, I want to share
follow-up technical work I have completed independently, in the spirit
of being a useful interlocutor rather than only an asker.

Before the technical content, one thing I want to be clear about. The
X2D 100C continues to produce images I could not make with any other
camera. The HNCS rendering at base ISO, the dynamic range I get in
mixed light, the mechanical feel of the XCD 55V focus ring, the
quietness of the leaf shutter — none of these are in question for me.
This letter is not a complaint about a camera I regret buying. It is
precisely because the X2D earns its place in everything else that the
AF behaviour stands out — it is the one part of the experience that
does not match the rest. That is why I am willing to spend my own
time on it, and why I think it is worth your time too.

In the days following my first letter, I conducted systematic direct
observation of my X2D 100C (firmware 4.2.0, XCD 2,5/55V), built an
open-source simulation testbench for PDAF autofocus decision policies,
and cross-referenced findings against published reviews and patent
literature. The work is at:

  https://github.com/Konamill-bot/x2d-pdaf-sim

I want to be transparent about what it is and is not. It is a *public*,
*reproducible*, *self-contained* study using synthetic scenes and a thin-
lens optical model. It is not a claim about Hasselblad's internal
implementation, and it is not a request that you adopt my code. It is
intended as a shared frame of reference so any technical discussion we
might have starts from the same definitions.

I want to lead with the most important finding from the full
lever-by-lever experiment (`scripts/run_imx461_stats.py`,
`out/imx461_full_stack_metrics.png`), based on 1000 independent
seeds per configuration on the 294-zone IMX461 simulator:
**the combination that produces a reliable win across both
low-contrast and high-contrast scenes is sensor binning + Kalman
temporal prior + multi-zone confidence aggregation.** Earlier
runs at fewer seeds showed misleading results (different seeds
favoured different configurations); reporting now with 1000-seed
means and standard deviations is what changed the recommendation.

All numbers below come from a simulation modelled on the Sony IMX461
sensor used in the X2D 100C: 21 × 14 = 294 PDAF zones tiled across
the active area, per-zone phase correlation at the native 3.76 μm
pitch (PDAF rows cannot be binned without destroying sub-aperture
phase signal), multi-zone configurations querying the nearest five
zones to subject and aggregating by confidence-weighted agreement.
Bar chart in `out/imx461_full_stack_metrics.png`. 1000 seeds per
configuration, mean standard error ~0.5 %.

Low-contrast static target (in-focus % = fraction of frames within
0.3 mm of target):

| Configuration                                  | in-focus %   |
|------------------------------------------------|--------------|
| A. baseline (stateless, 15 fps, single zone)   | 0.0 ± 0      |
| B. + 4x4 binning (60 fps) only                 | 0.0 ± 0      |
| C. + Kalman temporal prior (single zone)       | 5.2 ± 11.3   |
| **D. + multi-zone aggregation (nearest 5)**    | **47.1 ± 35.4** |
| E. + deadband + PID + CDAF fusion (V3 stack)   | 36.6 ± 42.2  |

High-contrast static target (same 1000 seeds):

| Configuration                                  | in-focus %     |
|------------------------------------------------|----------------|
| A. baseline                                    | **64.6 ± 42.5** |
| C. + Kalman                                    | 98.2 ± 0.9     |
| **D. + multi-zone**                            | **98.5 ± 0.4** |
| E. + V3                                        | 0.8 ± 8.8 (currently broken; see below) |

I want to call out one specific number in the high-contrast table:
**the baseline scores 65 ± 43 %**, meaning the current single-frame
PSR-threshold policy succeeds on roughly two thirds of the seeds and
fails catastrophically on the rest, even on high-contrast scenes
where it should succeed comfortably. This statistical signature
matches a direct observation I made on my own X2D: the same scene,
half-pressed multiple times, sometimes locks immediately and
sometimes hunts. The simulation reproducing this stochasticity from
first principles (stateless single-frame decisions on noisy PDAF
correlations) is the only point in this study where my synthetic
model and direct camera behaviour cross-validated independently.
That gives me modest confidence the rest of the model's predictions
are at least in the right qualitative ballpark.

The four non-obvious findings from this matrix:

1. **AF-readout framerate alone (config B) does not help — it makes
   hunting worse.** Without a temporal prior to integrate measurements,
   raising the framerate from 15 to 60 fps simply quadruples the rate
   at which low-confidence single-frame decisions are made. Lens
   motor travel rises from 5.8 mm to 23.8 mm in 2 seconds. Binning
   and temporal prior must be paired.

2. **Configuration D is the recommended target.** Binning + Kalman +
   nearest-5 multi-zone aggregation produces 47 ± 35 % in-focus on
   low-contrast and 98.5 ± 0.4 % on high-contrast (over 1000
   independent seeds on the 294-zone IMX461 simulator, mean SEM ~0.5 %).
   Compute cost is on the order of microseconds per frame on any
   modern application-class processor. To put it more precisely:
   **configuration D is not a proposal for better AF-S. It is, in
   effect, a working AF-C loop — a Kalman-filtered, multi-zone,
   high-framerate decision system that tracks focus continuously
   across frames.** The only difference between this simulation and
   an in-camera AF-C implementation is the firmware layer that
   enables it.

3. **Multi-zone aggregation is essential on low-contrast, not optional.**
   Configuration C (Kalman without multi-zone) achieves only 5 ± 11 %
   in-focus on low-contrast — effectively failure. The 9× lift from
   adding nearest-5 aggregation is the single largest improvement in
   the matrix. On high-contrast scenes C and D are statistically
   equivalent (98.2 ± 0.9 vs 98.5 ± 0.4), so multi-zone is free on
   easy scenes and necessary on hard ones — a dominant strategy.

4. **The full V3 stack (config E) is currently not recommended.**
   On the 294-zone IMX461 simulator E scores 37 ± 42 % on low-contrast
   and 1 ± 9 % on high-contrast. The deadband and PID parameters that
   worked on the earlier single-strip simulation interact badly with
   multi-zone aggregated confidence inputs; investigation continues
   in the repository. I include it here for transparency rather than
   as a recommendation.

The two conditional caveats from the first lever still apply: ISP
scheduling load and internal bus bandwidth between sensor and SoC are
factors only Hasselblad can measure, so configuration D is stated as
*if* the AF loop can be driven at 60 fps via PDAF channel
configuration, *then* the result above holds. The X2D's sensor is a
Sony IMX461 whose datasheet explicitly documents support for vertical
subsampling and horizontal pixel binning for high-speed 12-bit output;
the bandwidth required for 4x4-binned 60 fps PDAF readout is
comparable to the full 100 MP readout already sustained at the
shutter event, suggesting the sensor side is not the constraint.

**Two specific sensor-architecture uncertainties I cannot resolve from
outside the camera.** Both affect how literally the simulation's
numbers should be taken:

(a) *Does the IMX461 preserve PDAF sub-aperture phase signal under
its 4x4 imaging binning mode?* The IMX461 product flyer published by
Sony Semiconductor Solutions confirms binning capability:
"16-bit digital output [enables] 102 MP still mode at 2.7 fps. In
addition, vertical sub-sampling binning and horizontal pixel binning
realize high-speed 12-bit digital output for shooting moving picture."
This documents the binning mode is intended for *moving-picture
imaging output*; it does not describe what happens to PDAF pixels
during that mode. PDAF-in-binned-mode behaviour is not in the public
flyer.

The existence of US 11523071 *"Disparity-preserving binning for phase detection autofocus in digital imaging systems"* (USPTO patent) is itself evidence that naive
binning destroys PDAF phase signal — that is precisely why the patent
exists, to describe a specific pixel-readout architecture that
preserves disparity through binning. Whether IMX461 implements the
techniques described in that or a comparable patent is not publicly
documented.

My simulation assumes PDAF rows can be read at native 3.76 µm pitch
during AF half-press, decoupled from any imaging binning the EVF
stream uses. If this assumption is incorrect on IMX461 specifically —
if its only binned readout mode is the combined one that averages
PDAF pixels with their neighbours — the binning lever of my proposal
is inapplicable as stated, and the conversation moves to: under what
sensor mode can PDAF be read at full native rate without imaging
binning interfering? This is a question Hasselblad's sensor and
firmware teams can answer authoritatively where I cannot.

(b) *What is the actual ISP-to-decision latency at 60 fps on the X2D?*
The simulation uses 2 frames (~33 ms) as a representative value, but
the real number depends on ISP pipeline scheduling, readout mode, and
firmware. A 1-frame latency would be more forgiving and a 3-frame
latency would degrade temporal-prior performance noticeably. I have
also confirmed that *naive* latency buffering without predict-forward
Kalman compensation breaks high-contrast performance entirely (config
D under naive 2-frame delay scores 0 ± 0 % in-focus in simulation,
documented in the repository). Production-quality latency handling
requires the policy to advance its state estimate forward by N frames
before applying the delayed measurement — the standard
predictive-AF maths used by every modern AF system.

**One general epistemic limit beyond the two above.** The X2D
firmware is closed; observed behaviour is consistent with the
techniques in configuration D being absent, but equally consistent
with them being present and limited elsewhere (tuning, ISP
scheduling, motor driver). The simulation demonstrates these
techniques are *algorithmically feasible* on 294-zone PDAF
architecture — it does not claim knowledge of Hasselblad's internal
codebase. Either reading makes the question worth asking.

These observations are anchored by direct comparison with my own Sony
A7 IV (which on an all-white wall produces a single ~0.7 second hunt
followed by a clear failure indicator), and by published evidence that
Fujifilm achieved substantial AF improvements from GFX 100S to
GFX 100S II without changing PDAF hardware — purely through what
Capture Integration called an "improved predictive AF algorithm."

A note on the X2D II 100C's LiDAR. LiDAR provides direct range and
is a real hardware capability. The specific failure modes I infer
from my X2D observations — a near-focus AF failure consistent with
PDAF correlation-peak broadening at zero defocus, and stochastic
same-scene behaviour consistent with stateless single-frame
decisions — are firmware-level and orthogonal to range sensing.
Configuration D would address them whether LiDAR is present or not.

A related observation. The X2D II 100C's continuous-AF mode is
currently supported on only seven lenses (XCD 25 V, 28 P, 38 V,
55 V, 75 P, 90 V, and 35-100 E), and only in leaf-shutter mode.
Hasselblad's own product communication attributes the limitation to
older XCD focus modules being unable to track quickly enough. This
is a useful data point: even on the body that ships AF-C, the
binding constraint is not the body's silicon but the mechanical
focusing layer of older lenses. The firmware-layer levers I describe
above are independent of that mechanical limitation and would lift
performance wherever the underlying lens can move at the required
rate.

(A side note, offered as informal signal rather than evidence.)
An owner in an Asian medium-format community recently published a
hands-on side-by-side comparison of the X2D, X2D II, GFX 100S, and
GFX 100 II. Their report observes that the X2D II's AF, while
"noticeably faster" than the original X2D, still appears slower than
the GFX 100S — a 2021 body with neither LiDAR nor a deep-learning
AF accelerator. This is one user's experience, not a controlled
benchmark, and the pattern would need broader corroboration before
being load-bearing. I mention it only because, if it is borne out,
it would point at the firmware layer rather than the hardware as
the binding AF-speed constraint — which is precisely what the rest
of this letter argues from the simulation side.

A note connecting this back to my Letter 1 AF-C question. The
components in configuration D — a Kalman temporal prior over focus
position, multi-zone confidence aggregation, and a higher AF
decision-loop framerate — are the same building blocks any
continuous-AF (AF-C) implementation requires. The repository also
contains a `V4Policy` (in `pdaf_sim/policy2d.py`) that adds a 2-D
subject bounding-box Kalman tracker on top of these, demonstrating
that subject persistence across brief occlusions and zone-to-zone
subject motion are likewise tractable on the simulated 294-zone PDAF
architecture. I am not suggesting these specific algorithms are what
should ship; only that the algorithmic question "can AF-C be done on
X2D-class hardware" appears to be answered affirmatively at the
software layer. If your eventual response to Letter 1 is that AF-C
is hardware-limited, this work would refocus the question on
*which* hardware layer specifically constrains it (ISP scheduling,
lens motor driver loop, bus bandwidth) so that the conversation
becomes specific rather than categorical. If your response is that
AF-C is a policy decision rather than a hardware ceiling, then the
two letters together describe one consistent technical proposal
rather than two separate asks.

My question remains the same as in Letter 1, only more specific:

**Is there a path under which firmware-level AF improvements to the
X2D 100C could be considered — whether as a paid capability upgrade,
a routine firmware revision, or a hardware-limitation reply that
honestly closes the topic?**

I am not asking for a position, and I am not asking for my code to
be adopted. I am asking for something simpler — acknowledgement that
the question is worth answering. A 20-year-old who spent his weekend
building this simulation instead of doing anything else did so because
this brand genuinely matters to him. That kind of engagement is rare,
and I would simply value knowing it was seen.

I do not expect a quick response; please take the time you need. I
plan to continue documenting findings on the public repository in the
same calm and non-confrontational tone as this letter, regardless of
how this conversation develops.

Thank you again for your time.

Sincerely,
Chan Kam Chi
Hasselblad 500C/A12 · X2D 100C (fw 4.2.0) · XCD 2,5/55V · Hasselblad Masters 2026
Public technical notes:
  github.com/Konamill-bot/x2d-cim-notes
  github.com/Konamill-bot/x2d-pdaf-sim   (this study)
