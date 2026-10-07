# af_c: the whole AF chain in C99

The camera-side AF code from x2d-pdaf-sim, written as embedded-style C: the eyes, the
tracker and the brain, glued into one call per frame.

```
PDAF L/R views --af_phase--> per-zone (disparity, confidence)
               --af_track (AF-T only)--> the subject's zones --combine--> one measurement
               --af_dual_gated--> lens command
```

| file | stage | Python reference model |
|---|---|---|
| `af_phase.[ch]` | **eyes**: per-zone phase correlation (radix-2 FFT, partial phase weighting, sub-pixel peak, PSR confidence) and the multi-zone combine | `pdaf_sim/phase_corr.py`, `pdaf_sim/zone_tracker.py::zone_estimates / combine_zones` |
| `af_track.[ch]` | **AF-T tracker**: which zones are the subject, using depth only | `pdaf_sim/zone_tracker.py::ZoneTracker` |
| `af_dual_gated.[ch]` | **brain**: calibrated, gated, two-timescale Kalman | `pdaf_sim/policy_dual.py::DualGated` |
| `af_chain.[ch]` | the loop: AF-C combines every zone; AF-T uses the tracker and coasts while the subject is hidden; `AF_MODE_ROI` follows a detector's box with one PDAF window | `scripts/run_aft_tracker.py::run_chain`, `scripts/run_subject_classes.py::run` |
| `af_roi.[ch]` | **subject-box AF**: the window an ISP measures for a box (`af_isp_fit`), and the ROI-led depth veto (`af_roi_gate`) | `pdaf_sim/roi.py` |

Simulation research code: it is not derived from any manufacturer's firmware (see
[DISCLAIMER.md](../DISCLAIMER.md)).

## Run it

```bash
cd af_c
make                      # libraries, ./bench, ./af_demo, ./test_c
./af_demo                 # pure C, closed loop: AF-C vs AF-T on a scene with an occluder
make check                # pure-C unit tests (no Python)
make asan                 # AddressSanitizer + UBSan builds: ./test_c_asan, ./af_demo_asan, ./bench_asan
./bench                   # cost of each stage per frame
python3 test_equiv.py     # every C stage vs its Python reference, on simulator data
python3 closed_loop.py 12 # the 4-phase simulator with every AF stage in C
```

Build flags: `-std=c99 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wshadow`. The code
builds clean with both gcc and clang.

`af_demo` is a self-contained C program. Its miniature world, also written in C, has a
textured subject swaying in depth, an opaque occluder that slides across the AF area twice,
defocus blur with the PDAF left/right shift, 3 frames of pipeline latency, and a servo
voice-coil lens. The world's optics are a simplified 1-D version of the Python simulator;
the C AF code is the real thing. Sample output:

```
  time  subject  occluder | AF-C lens     | AF-T lens
  3.13    1.04    partial |  1.51 OUT     |  1.05 in
  3.20    1.02     FULL   |  2.59 in      |  1.03 in  (holding)
  ...
  4.33    1.03    partial |  2.62 OUT     |  0.99 in
  4.40    1.04       -    |  1.16 OUT     |  1.05 in
```

AF-C moves to the occluder while it fills the AF area. AF-T holds the subject and picks it
straight back up afterwards. In the first crossing the subject reverses direction while
hidden, and coasting at constant velocity drifts off; the demo prints that limit instead
of hiding it.

## API

```c
#include "af_chain.h"

static af_chain ch;                                    /* ~20 KB, no heap */
af_chain_init(&ch, AF_MODE_T, 256, 64, 14, 48, 1.7185f); /* width, height, zones, max disparity px, px per mm */
cmd = af_chain_frame(&ch, L, R, lens_at_exposure);     /* each frame with PDAF views */
cmd = af_chain_coast(&ch);                             /* each frame without */
cmd = af_chain_frame_zones(&ch, disp_px, conf, lens);  /* or: per-zone results from a PDAF block */
```

Each stage is also usable on its own (`af_pc_*`, `af_track_*`, `af_step / af_coast /
af_predict`). `af_chain_frame_zones` is for a sensor or ISP that does phase detection in
hardware and delivers per-zone disparity and confidence: it skips the software eyes and
runs the rest of the chain unchanged (`make check` compares it with `af_chain_frame`).

Subject-box AF (`AF_MODE_ROI`), when a detector supplies the subject's box:

```c
static af_chain ch; af_isp isp; af_win win;
af_chain_init(&ch, AF_MODE_ROI, 512, 64, 1, 24, 1.7185f);
af_isp_default(&isp);                                  /* placeholder rules: replace with the ISP's */
win = af_isp_fit(&isp, box, 512, 64);                  /* the PDAF window to program for the box */
cmd = af_chain_frame_roi(&ch, has, disp_px, conf, lens); /* that window's result, once per frame */
cmd = af_chain_frame_box(&ch, L, R, win, lens);        /* ...or measure the window in software */
```

The window's measurement is used unless its depth disagrees with the track; then it is vetoed
for at most 5 frames, after which the detector wins (`af_roi_gate`). `has = 0` (no box this
frame) coasts the brain. For an ISP that returns several zones of one ROI (ISP model B in the main
README), pass the chosen zone's result, or `af_pc_combine` of several, to `af_chain_frame_roi`.
The zone layout itself (`pdaf_sim/roi.py: ZonedRoi`) is not ported to C yet.

- **C99.** No heap and no global state. Fixed work per frame.
- **Memory:** `af_state` is 116 B, `af_track` 72 B, and `af_pc` 19.5 KB (FFT twiddles,
  window and scratch for up to 512-pixel-wide views).
- **Precision:** `float` by default; `-DAF_REAL_DOUBLE` builds in double.

## Verification

**`test_equiv.py`: each C stage against its Python reference**, on inputs recorded from the
4-phase simulator:

| stage | data | double build | float build |
|---|---|---|---|
| brain | 53,619 step / coast / predict calls | max \|diff\| 3.6e-15 mm | 2.3e-6 mm |
| eyes | 16,730 zone estimates, all four phases | max \|diff\| 4e-5 px, **0** correlation-peak flips | 1e-5 px, **0** flips |
| tracker | 17,619 decisions | **0** differ | **0** differ |
| chain | 600 frames of P3 views | 100% of commands within 1e-3 mm | 100% |

The Python eyes do their row averaging in float32, so the float build matches them about as
closely as the double build does.

**`closed_loop.py`: the simulator driven by C stages vs Python stages** (12 seeds, mean
difference ± sem in in-focus points):

| | P1 | P2 | P3 | P4 | overall |
|---|---|---|---|---|---|
| AF-C, C float − Python | −0.04 | +0.01 | −0.00 | +0.03 | +0.01 ± 0.01 |
| AF-T, C float − Python | −0.05 | −0.04 | −0.62 ± 0.52 | +2.59 ± 1.89 | +0.90 ± 0.73 |
| AF-T, **C double** − Python (control) | −0.03 | −0.01 | +0.23 ± 0.46 | +2.09 ± 1.53 | +0.87 ± 0.59 |

Closed loop, one tiny difference can flip one discrete decision (a gate, a hold), and from
then on the trajectory forks. AF-T has more such decisions, so it spreads more. The double
build is the control: it shows the same spread as the float build. That makes the spread
forking, not single precision, and none of these differences is significant.

**`test_roi.py`: subject-box AF** (`af_roi.c`, `af_pc_window`, `AF_MODE_ROI`) against `pdaf_sim/roi.py` and the class study, with the ISP rules of `scripts/run_isp_window.py`:

| check | result |
|---|---|
| window rules (`af_isp_fit`), 20,000 random boxes and rules | 0 differ |
| window phase correlation, 2,824 windows from 4 clips | 99.9th pct \|diff\| 1.6e-6 px, 0 peak flips |
| veto + brain, 7,176 recorded calls replayed | 1.6e-15 mm (double), 1.2e-6 mm (float) |
| closed loop, float C chain vs Python, 4 classes × 5 seeds | within 0.24 in-focus points |

**Also checked:** `make check` (54 pure-C checks, float and double) and ASan + UBSan builds,
all clean.

## Cost and size

| | result |
|---|---|
| eyes, 14 zones of 64 × 256 views | ~200 µs per frame on one x86-64 core (-O2) |
| tracker | ~0.65 µs per frame (including the benchmark's input generation) |
| brain | ~34 ns per frame |
| whole chain, AF-T | **~200 µs per frame = 1.2% of a 60 fps frame** |
| subject box (`AF_MODE_ROI`): a 48 × 16 window measured in software, veto, brain | ~3.8 µs per frame (~4.7 µs if the window size changes every frame) |
| subject box, from an ISP's window result: veto, brain | ~33 ns per frame |
| ARM Cortex-M4F (FPv4-SP hard-float, -Os) | `.text` 7.1 KB for all four modules, hardware FPU; needs only `cosf sinf expf tanhf powf memcpy memset` |
| ARM Cortex-A53 (-O2) | `.text` 26 KB (the loops are unrolled and vectorised) |

In a camera the phase correlation (the eyes) would normally run in the sensor or ISP
hardware. The ~200 µs here is the cost of doing it in software. The decision stages (tracker
and brain) cost under a microsecond.

The ARM objects were built with `zig cc` (`pip install ziglang`):

```bash
python -m ziglang cc -target thumb-linux-musleabihf -mcpu=cortex_m4+vfp4d16sp -mfloat-abi=hard -Os \
    -fno-unwind-tables -fno-asynchronous-unwind-tables -std=c99 -c af_*.c
python -m ziglang cc -target aarch64-linux-musl -mcpu=cortex_a53 -O2 \
    -fno-unwind-tables -fno-asynchronous-unwind-tables -std=c99 -c af_*.c
```

## Things found on the way

- **`-mcpu=cortex_m4` alone compiles to software floating point.** Every add and multiply
  became an `__aeabi_f*` call. The FPU feature has to be named (`+vfp4d16sp`), and the
  disassembly checked.
- **FFT twiddles computed in `double` pulled the soft-double library into the M4F build.**
  They run once, at init, but that still costs code size. They are now computed in the
  build's own precision.
- **Single-precision Kalman covariance can lose positive-definiteness** after a re-init with
  a wide prior. The brain has a guard. It fired 0 times in testing (counted with
  `-DAF_COUNT_PD_FIXES`).
- **A closed-loop comparison cannot check fixed-point logic exactly.** With the ISP's 1/16 px output, a 1e-12 difference
  crossed a rounding step and forked 2 of 8 clips. `test_roi.py` replays recorded calls open-loop instead.
- **Python's `estimate_disparity` returns a meaningless value on a window too narrow for its ±max_disp search.**
  `af_pc_window` refuses it, and the ISP's minimum window width keeps every window valid.
- **`-Werror` caught `af_median(n = 0)` reading uninitialised memory.** No caller passes 0,
  but the function now defends itself.
