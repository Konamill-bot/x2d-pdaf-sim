# Findings — Hasselblad X2D 100C autofocus behavioural study

A record of direct observations of the Hasselblad X2D 100C (firmware 4.2.0)
with XCD 2,5/55V, organized by causal layer. Each observation is independent,
externally verifiable, and (where applicable) cross-referenced with public
reviews, technical patents, or peer-camera reference behaviour.

Observer: Chan Kam Chi
Reference camera: Sony A7 IV (own)
Observation date: 2026-06-08

---

## Layer 1 — PDAF decision policy

These four observations all point at the same underlying mechanism — how the
firmware decides whether to trust a given PDAF measurement.

### 1.1 PDAF works on high-contrast subjects
- **Observation**: aimed at a card with printed text, X2D performs a single
  PDAF jump followed by a small CDAF refine. Total time is short and
  qualitatively comparable to A7 IV in the same scene.
- **Interpretation**: PDAF hardware and PDAF→lens pipeline are functional.
  This rules out "PDAF isn't being used at all" as a hypothesis.

### 1.2 Marginal contrast triggers CDAF hill-climb
- **Observation**: on subjects with moderate contrast, X2D first performs a
  slow exploratory motion, then accelerates in one direction toward the
  focus peak. This is the signature pattern of contrast-detection hill-climb,
  not PDAF jump-then-refine.
- **Interpretation**: PDAF confidence threshold (`tau_trust` in the
  testbench's `BehavioralPolicy`) is set conservatively. Scenes where PDAF
  could give a usable estimate are demoted to CDAF fallback unnecessarily.
- **Cross-reference**: corroborates published reviews describing X2D as
  "slow and basic by modern standards" (Lloyd Chambers) and "hunts a lot
  in dimmish light with low contrast subjects" (Jim Kasson).

### 1.3 Same-scene behaviour is stochastic
- **Observation**: in identical scenes, X2D sometimes performs a clean PDAF
  snap; other times it falls back to CDAF hunt. Behaviour appears to depend
  on the precise frame at which the half-press is sampled.
- **Interpretation**: the policy operates on single-frame confidence with
  no temporal accumulation. Frame-to-frame noise in PDAF confidence pushes
  the system across the trust/fallback boundary stochastically.
- **TBD**: confirm with tripod + remote release (Test D) to rule out
  hand-shake at half-press as the cause.

### 1.4 Near-focus failure (most diagnostic single observation)
- **Observation**: when the lens is already close to focus, X2D still
  performs slight hunting and then displays the red AF-failure indicator.
- **Interpretation**: classic PDAF corner case. As defocus approaches zero,
  the L/R sub-aperture images converge; the phase-correlation surface peak
  remains at zero but broadens. Peak-to-sidelobe-ratio (PSR) confidence
  drops *because* the system is in focus, triggering the same fallback
  used for genuine low-confidence scenes. CDAF then has no gradient to
  climb and times out.
- **Cross-reference**: the industry recognises this failure mode — multiple
  US patents address PDAF "noise reduction" (US9729779, US9420164) and
  "focus hunting prevention" (US9910247). The IS&T paper *"Improving the
  Reliability of Phase Detection Autofocus"* discusses confidence handling
  near focus.
- **Why it matters**: this is the X2D failure that is most subjectively
  jarring to users (it fails *because* it succeeded) and is also the most
  inexpensive to fix at the firmware level.

### Common fix for 1.2 / 1.3 / 1.4
All three reduce to a single architectural change: replace the stateless,
single-frame PSR threshold with a stateful decision that accumulates
confidence over time and includes peak-position information (not just
peak sharpness). This is what `pdaf_sim.policy.BehavioralPolicy`
demonstrates in the testbench.

---

## Layer 2 — PDAF zone selection

### 2.1 Apparent central bias
- **Observation**: the AF appears to predominantly act on the central
  region of the frame, despite Hasselblad's spec listing 294 PDAF zones
  covering 97% of the sensor.
- **TBD**: confirm with Auto Area mode and an off-centre target (Test E).
  If the AF box follows the off-centre subject, this is just user-mode
  selection and not a real issue. If the AF box stays central, there
  is a genuine zone-selection gap between spec and behaviour.

---

## Layer 3 — Subject understanding (out of scope)

### 3.1 No subject persistence
- **Observation**: the system does not appear to recognize that successive
  frames contain the same subject.
- **Interpretation**: X2D lacks a subject-detection / tracking model.
  This is consistent with the X2D II 100C marketing, which advertises
  new "deep learning subject detection" as a flagship feature.
- **Scope note**: this gap requires a trained ML model, not a firmware
  parameter change. Outside the scope of what this study advocates for —
  it would amount to asking Hasselblad to ship the X2D II's product
  differentiator on the X2D.

---

## X2D II 100C as Hasselblad's own answer

The X2D II 100C, launched 2025, addresses AF performance by **adding
hardware**: a DJI-derived LiDAR module (one transmit dot + one receive
dot on the front panel, joining the existing AF illuminator for a total
of three visible front-facing elements vs. the X2D 100C's one), plus an
increase from 294 to 425 PDAF zones, plus a deep-learning continuous
AF algorithm.

This constitutes an implicit acknowledgement that the X2D 100C's AF
performance had room for improvement.

**However**, the failure modes catalogued in Layer 1 above are not
fundamentally range-finding problems and would not be solved by LiDAR
alone:

- **Near-focus PSR confidence drop (1.4)**: LiDAR provides distance,
  not contrast peak sharpness. The confidence-metric design flaw
  persists independently of any range hardware.
- **Stateless single-frame decision (1.3)**: a firmware-architectural
  decision, orthogonal to whether range is measured by PDAF, LiDAR,
  or both.
- **Conservative `tau_trust` (1.2)**: again a firmware threshold,
  unrelated to range sensing.

The implication: the firmware improvements implied by the Layer 1
findings have **independent value** beyond what LiDAR provides. They
would have improved X2D 100C AF without requiring the LiDAR hardware
of X2D II, and they would *also* improve X2D II AF stacked on top of
its LiDAR pipeline.

Cross-references:
- [DJI LiDAR tech in Hasselblad X2D II (DroneXL)](https://dronexl.co/2025/06/08/hasselblad-x2d-100c-ii-dji-lidar-tech/)
- [Hasselblad X2D II 100C product page](https://www.hasselblad.com/x-system/x2d-ii-100c/)

## Hardware ceiling: ruled out

### Lens motor
The XCD 55V uses a **linear stepping motor** with a "lighter, smaller
focusing lens group." Hasselblad's own product page states the design
"effectively eliminates any backlash" and that "the focusing lens group
can quickly reach the focusing position and achieve a precise stop"
([Hasselblad XCD 55V](https://www.hasselblad.com/x-system/lenses/xcd-55v/)).

The mechanical hardware is best-in-class for medium format. Observed
slow / hunting behaviour cannot be attributed to motor limitations.

### Subsampled / binned readout pipeline, and the separate PDAF channel
The X2D's published EVF (5.76 M-dot, ~1.9 MP) and rear LCD (2.36 M-dot,
~0.8 MP) are driven in real time from the 100 MP sensor during live
view. This is only physically possible if the sensor is operating in
a subsampled / binned readout mode at high framerate -- a full 100 MP
readout could not be sustained at EVF refresh rates.

Architectural note: on-sensor PDAF uses a readout *channel* that is
distinct from the imaging pixel channel. PDAF pixels are masked or
split-photodiode pixels embedded within the imaging array, and binning
them with their neighbours destroys the sub-aperture phase signal.
Modern Sony IMX-class sensors therefore expose PDAF row data through
a separate readout path whose framerate is a register/firmware
configuration, decoupled from both the full-image readout and the
subsampled image readout that drives the EVF.

Consequence -- with an honest caveat:

EVF refresh rate is not direct evidence of PDAF readout rate. What it
shows is that the *sensor* side of the pipeline supports high-framerate
output without thermal or bandwidth catastrophe. The PDAF channel
itself is architecturally separate and its rate is a firmware/register
choice.

However, total AF loop framerate is gated by more than sensor readout
and PDAF rate: it also depends on ISP scheduling load (JPEG encode,
IBIS computation, noise analysis, EVF compositing may all compete for
ISP cycles) and on internal bus bandwidth between sensor and SoC.
These are only measurable from inside the camera. From outside we can
say:
  - Sensor side: demonstrably not the bottleneck (EVF runs).
  - PDAF readout channel: architecturally separable; rate is a
    firmware choice.
  - ISP scheduling and bus bandwidth: unknown -- could be the actual
    constraint on AF loop framerate.

The simulation finding ("at 60 fps AF loop, hunting collapses to
zero") is therefore best stated conditionally: *if* the AF loop can
be driven at 60 fps, *then* hunting is eliminated. Whether the
antecedent holds in the X2D is a firmware-level investigation only
Hasselblad can perform.

### Image processor (ISP)
The decision-policy changes implied by Layer 1 (temporal Kalman, modified
confidence metric) are O(microseconds) on a Cortex-A class CPU and are
independent of the 100MP sensor readout pipeline. They do not increase
PDAF computation, only re-use existing PDAF output more intelligently.

### ISP budget estimate — AF-C decision stack vs. loads the X2D already runs

Order-of-magnitude estimate, using the camera's own shipped features
as the yardstick. All figures are back-of-envelope and labelled as
such; corrections welcome.

**Bandwidth:**

| load | data rate | status |
|---|---|---|
| EVF live stream (4x4 binned, ~6.4 MP x 60 fps, 10-bit) | ~480 MB/s | shipped — runs today |
| all 294 PDAF zones read every frame (upper bound) | ~100 MB/s | nobody needs this |
| AF-C actual need (5 zones x L/R x 60 fps) | ~2 MB/s | 0.4 % of the EVF stream |

The PDAF channel is a separate readout path (see above). The 5-zone
AF-C data rate is a rounding error against the EVF stream the camera
already sustains. Bus bandwidth cannot be the AF-C blocker, because a
load ~250x larger is already running.

**Compute:**

| load | order | status |
|---|---|---|
| face detection (mobile-class detector, even at 15 Hz) | ~1.5–15 GFLOPS | shipped — firmware 3.1.0 (2023-11-30) |
| phase correlation, 5 zones x 60 fps (2-D FFT, worst case) | ~0.3–0.6 GFLOPS | ~1/10 to 1/25 of face detection |
| Kalman policy itself (2x2 matrices) | ~3 kFLOPS | ~10^-6 of face detection |

Real PDAF correlators are typically 1-D SAD over masked-pixel lines,
often a hardware block inside the ISP — so the 2-D FFT figure above
overstates the cost.

**What remains honestly unknown** is the same scheduling question as
above: whether the AF loop gets a guaranteed time slot when JPEG
encode, IBIS, and noise processing compete for ISP cycles. That is a
software-integration question, measurable only inside the camera.
But "the ISP lacks the capacity" is contradicted by the loads the
camera already ships: it streams ~480 MB/s to the EVF and spends
GFLOPS-class compute on face detection. The AF-C decision stack's
marginal cost — ~2 MB/s and microseconds of Kalman per frame — is
smaller than either by orders of magnitude.

---

## Cross-camera reference

### Sony A7 IV (own, same firmware era)
- **T1 white wall**: hunts ~0.7s, then displays purple AF-box failure
  indicator. One sweep pass.
- **Implication**: a hard timeout + explicit failure UX is industry standard.
  X2D already implements this (red box after 2 passes), but with a slower
  / longer-failing variant.

### Fujifilm GFX 100S / 100S II (published reference)
- GFX 100S → GFX 100S II improved AF *without* changing PDAF hardware.
  The improvement was attributed to "improved predictive AF algorithm"
  ([Capture Integration](https://www.captureintegration.com/fujifilm-gfx-100s-ii-profoundly-better-autofocus/)).
- **Implication**: at least one peer manufacturer has demonstrated that
  100MP-class PDAF AF can be substantially improved via firmware alone.

### Blackmagic Cinema 6K (published reference — strongest precedent)
- A **free firmware update (v9.5, 2025)** added **continuous autofocus,
  object tracking and face detection** to a camera owners already had,
  using AI processing on the existing hardware (the AF motors live in
  the L-mount lens)
  ([Digital Camera World](https://www.digitalcameraworld.com/cameras/cinema-cameras/the-fan-favorite-blackmagic-cinema-6k-camera-is-about-to-get-a-major-autofocus-overhaul-with-a-free-firmware-update)).
- The first build was admittedly buggy; Blackmagic then refined it with
  the community, rolling user AF-C feedback into successive builds.
- **Implication**: this is the closest published precedent to what the
  X2D study argues — continuous AF, including face detection, delivered
  to an *existing* body by firmware on hardware it already shipped with.

### Nikon Z9 / Z6 III (published reference)
- The Z9 is ~4 years old and Nikon is *still* shipping major free
  firmware: v4.0 alone listed 25+ new features, and flagship features
  (bird detection, pre-release capture) have been pushed *down* into
  cheaper bodies like the Z6 III via firmware
  ([DPReview](https://www.dpreview.com/news/5858543313/nikon-z9-firmware-5p3-update-features),
  [Digital Camera World](https://www.digitalcameraworld.com/tech/firmware/the-nikon-z8-and-z6-iii-just-gained-a-long-list-of-new-custom-features-and-bug-fixes-thanks-to-firmware)).
- **Implication**: the prevailing direction among peers is firmware that
  *narrows* the gap between old and new bodies, not one that gates
  capability behind a new purchase.

### Paid-upgrade precedent (for the "licensed upgrade" proposal)
- Manufacturers already charge owners to unlock features on hardware they
  own: Sony's US$149 custom-gridline license, and Panasonic's **DMW-SFU2
  key** which unlocks V-Log (a pro video feature the hardware can already
  do) on the LUMIX S1
  ([PetaPixel](https://petapixel.com/2023/11/28/for-150-sony-will-let-you-add-custom-gridlines-to-your-a7-iv/),
  [Panasonic](https://na.panasonic.com/news/panasonic-lumix-to-release-the-upgrade-software-key-dmw-sfu2)).
- **Note**: Phase One and Leica also run official paid *upgrade* paths,
  but those are hardware trade-ups, a looser fit than a paid firmware
  feature license.

---

## Sources

- [Lloyd Chambers (diglloyd) — X2D Autofocus](https://diglloyd.com/prem/s/MF/HasselbladX/HasselbladX2D-autofocus.html)
- [Jim Kasson — X2D vs GFX 100 II family photography](https://blog.kasson.com/x2d/family-photography-with-the-x2d-xcd90v-and-the-gfx-100-ii-gf110/)
- [Roman Fox — Hasselblad X2D 100C Review](https://www.snapsbyfox.com/blog/hasselblad-x2d-100c-review)
- [Capture Integration — GFX 100S II profoundly better AF](https://www.captureintegration.com/fujifilm-gfx-100s-ii-profoundly-better-autofocus/)
- [Digital Camera World — Blackmagic Cinema 6K free firmware adds continuous AF](https://www.digitalcameraworld.com/cameras/cinema-cameras/the-fan-favorite-blackmagic-cinema-6k-camera-is-about-to-get-a-major-autofocus-overhaul-with-a-free-firmware-update)
- [DPReview — Nikon still adding features to the Z9 four years on](https://www.dpreview.com/news/5858543313/nikon-z9-firmware-5p3-update-features)
- [Digital Camera World — Nikon Z8 / Z6 III firmware feature additions](https://www.digitalcameraworld.com/tech/firmware/the-nikon-z8-and-z6-iii-just-gained-a-long-list-of-new-custom-features-and-bug-fixes-thanks-to-firmware)
- [PetaPixel — Sony's US$149 custom gridline license](https://petapixel.com/2023/11/28/for-150-sony-will-let-you-add-custom-gridlines-to-your-a7-iv/)
- [Panasonic — DMW-SFU2 paid V-Log upgrade key](https://na.panasonic.com/news/panasonic-lumix-to-release-the-upgrade-software-key-dmw-sfu2)
- [Photrio thread — X2D focusing help (incl. banning incident)](https://www.photrio.com/forum/threads/hasselblad-x2d-focusing-help.200327/)
- [Hasselblad XCD 55V product page](https://www.hasselblad.com/x-system/lenses/xcd-55v/)
- [USPTO US9910247 — Focus hunting prevention for PDAF](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9910247)
- [USPTO US9729779 — PDAF noise reduction](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9729779)
- [IS&T — Improving the Reliability of Phase Detection Autofocus](https://library.imaging.org/admin/apis/public/api/ist/website/downloadArticle/ei/30/5/art00006)
