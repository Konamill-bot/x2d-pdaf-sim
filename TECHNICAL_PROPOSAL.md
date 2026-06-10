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
low-contrast and high-contrast scenes is binned high-framerate AF
readout + Kalman temporal prior + multi-zone confidence
aggregation.** Two methodology notes for transparency: early runs
at few seeds favoured different configurations run-to-run, so
everything below is 1000-seed means and standard deviations; and the
simulation went through three model revisions (fair baseline, fixed
scene per trial, motor rate limiting, sub-pixel disparity rendering)
each of which *changed the numbers but not the ranking* — the
revision history is preserved in the repository's DEV_LOG.

All numbers below come from a simulation modelled on the Sony IMX461
sensor used in the X2D 100C: 21 × 14 = 294 PDAF zones tiled across
the active area, per-zone phase correlation at the native 3.76 μm
pitch (PDAF rows cannot be binned without destroying sub-aperture
phase signal — disparity is rendered with exact sub-pixel shifts),
multi-zone configurations querying the nearest five zones to subject
and aggregating by confidence-weighted agreement. The simulation
includes lens motor rate limiting (~18 mm/s focus travel — no
teleporting lens), a fixed scene per trial (measurement errors are
*not* i.i.d. across frames, so the temporal prior gains no
unrealistic advantage), and a fair baseline whose CDAF fallback
scans monotonically through the travel range rather than jittering
in place. Bar chart in `out/imx461_full_stack_metrics.png`. 1000
seeds per configuration, mean standard error ~0.2 %.

Low-contrast static target (in-focus % = fraction of the 2-second
window with lens within 0.3 mm of target):

| Configuration                                    | in-focus %   |
|--------------------------------------------------|--------------|
| A. baseline (stateless scan, 15 fps, single zone)| 8.9 ± 5.1    |
| B. + binned AF readout (60 fps) only             | 9.2 ± 0.6    |
| C. + Kalman temporal prior (single zone)         | 91.1 ± 5.5   |
| **D. + multi-zone aggregation (nearest 5)**      | **92.0 ± 0.5** |
| E. + deadband + PID + CDAF fusion (V3 stack)     | 91.4 ± 4.7   |
| F. = D + 2-frame ISP latency, compensated        | 77.2 ± 1.6   |

High-contrast static target (same 1000 seeds):

| Configuration                                    | in-focus %   |
|--------------------------------------------------|--------------|
| A. baseline                                      | 90.4 ± 6.7   |
| B. + binned readout                              | 93.6 ± 0.6   |
| C. + Kalman                                      | 94.2 ± 0.0   |
| **D. + multi-zone**                              | **94.2 ± 0.0** |
| E. + V3 stack                                    | 92.6 ± 0.4   |
| F. = D + 2-frame latency, compensated            | 92.5 ± 0.0   |

The cross-validation I find most convincing is two-sided. The
simulated baseline behaves like my real X2D on *both* ends of the
difficulty range: on high-contrast scenes it usually locks but with
visible run-to-run variance (90.4 ± 6.7 — the same scene,
half-pressed repeatedly, sometimes snapping and sometimes hesitating,
exactly what I observe on the camera), and on low-contrast scenes it
spends most of the window hunting (8.9 %), which is the user-reported
pain case. The simulation was not tuned to reproduce either
behaviour; both emerge from first principles (single-frame threshold
decisions on noisy PDAF correlations). That two-sided match is the
strongest reason I have to believe the model's *relative* comparisons
are in the right qualitative ballpark.

The five findings from this matrix:

1. **Higher AF framerate alone (config B) buys almost nothing**
   (8.9 → 9.2 % on low contrast). Without a temporal prior to
   integrate measurements, faster readout just repeats the same
   low-confidence single-frame decision more often — the scan covers
   four times the lens travel (6.2 → 24.0 mm in 2 s) for the same
   outcome. Binning and the temporal prior must be paired.

2. **The Kalman temporal prior is the hero lever** (8.9 → 91.1 % on
   low contrast). Compute cost is on the order of microseconds per
   frame on any modern application-class processor. To put it more
   precisely: **configurations C-F are not proposals for better
   AF-S. They are, in effect, a working AF-C loop — a
   Kalman-filtered, high-framerate decision system that tracks focus
   continuously across frames.** The only difference between this
   simulation and an in-camera AF-C implementation is the firmware
   layer that enables it.

3. **Multi-zone aggregation's value is reliability, not raw mean.**
   Adding nearest-5 aggregation (config D) lifts the low-contrast
   mean only modestly (91.1 → 92.0 %) but cuts the run-to-run
   standard deviation by an order of magnitude (± 5.5 → ± 0.5). For
   a photographer this is the difference between "usually locks" and
   "locks every time" — consistency is precisely what the X2D's AF
   is criticised for lacking. (An earlier iteration of this study
   claimed a 9× mean lift from multi-zone; that was an artifact of a
   flawed aggregation formula, corrected in the repository history.)

4. **The smoothness stack (config E: deadband + PID + CDAF fusion)
   is viable as a refinement, not a requirement.** It performs on
   par with D (91.4 / 92.6 %) while halving residual sweep events,
   at slightly higher final error. An earlier version of this study
   reported E as broken; that was caused by simulation measurement
   artifacts since fixed, and I note the correction for transparency.

5. **ISP pipeline latency is handled by standard bookkeeping, not a
   blocker.** Config F adds a 2-frame (~33 ms) measurement delay
   with timestamp-correct association — each delayed measurement is
   fused against the lens position at capture time. Result: a modest
   duty-cycle cost on low contrast (92.0 → 77.2 %, mostly the slower
   ramp-in) and essentially nothing on high contrast (92.5 %).
   Without that bookkeeping the same latency is catastrophic
   (0 % in-focus; see `scripts/run_latency_sweep.py`). This is the
   predictive-AF arithmetic every modern AF system implements.

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
firmware. Both sides of this uncertainty are now demonstrated in the
simulation rather than asserted (`scripts/run_latency_sweep.py`,
300 seeds): *naive* fusion of delayed measurements against the
current lens position degrades severely by 2 frames of latency
(41 ± 16 % low contrast, 32 ± 0.3 % high contrast — the policy
chases stale references), while the same latency handled with
timestamp-correct measurement association — each delayed measurement
fused against the lens position at capture time — degrades
gracefully (77.1 / 92.5 % at 2 frames, 69.8 / 91.7 % even at 3).
This is the standard predictive-AF bookkeeping every modern AF
system implements; the open question is only what N is on the X2D
pipeline, which shifts the duty-cycle numbers but not the
conclusion.

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

One specific consequence of that lens list is worth stating plainly:
**my own XCD 2,5/55V is on it.** The lens I observe hunting on the
X2D 100C is the same linear-stepping-motor design that Hasselblad has
certified for continuous AF on the X2D II. On my exact setup, the
mechanical layer is demonstrably not the constraint — the lens
hardware is AF-C-capable by Hasselblad's own qualification. The
delta between my camera hunting on a static subject and the X2D II
tracking a moving one is confined to the body: its firmware, and
whatever ISP-scheduling budget that firmware is given.

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
