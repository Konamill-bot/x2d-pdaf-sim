# af_c: the DualGated AF-C policy in C99

The C port of `pdaf_sim/policy_dual.py::DualGated`. That policy is a confidence-calibrated,
innovation-gated, two-timescale Kalman AF-C decision policy; the README section
*"DualGated"* explains why it is built that way. The Python class is the reference model.
This directory holds the embedded-style implementation, plus the tests that tie the two
together.

Simulation research code: it is not derived from any manufacturer's firmware (see
[DISCLAIMER.md](../DISCLAIMER.md)).

## API

```c
#include "af_dual_gated.h"

af_params p;  af_state st;
af_default_params(&p);            /* the validated parameters */
af_init(&st, &p);

/* once per AF-loop tick, exactly one of: */
cmd = af_step(&st, d, c, lens);   /* PDAF measurement: defocus d (mm), PSR confidence c,
                                     lens position (mm) at the time it was exposed */
cmd = af_coast(&st);              /* no measurement this tick (dropout, subject hidden) */
```

- **C99.** No heap and no global state (apart from the opt-in `-DAF_COUNT_PD_FIXES` test
  counter). One `af_state` (116 bytes) per AF instance.
- **Fixed work per tick.**
- **Dependencies:** `powf`, once per measurement for σ(c) = 0.036 · c^−0.93; and `sqrtf`, in a
  covariance guard that never fired in testing.
- **Precision:** `float` by default. Build with `-DAF_REAL_DOUBLE` for double; the
  equivalence test uses that build.

## Build and test

```bash
cd af_c
make                         # libafdg_f.so, libafdg_d.so (ctypes), bench
./bench                      # per-tick cost
make asan && ./bench_asan    # AddressSanitizer + UndefinedBehaviorSanitizer
python3 test_equiv.py        # C vs the Python reference, call by call
python3 closed_loop.py 8     # the full simulator driven by the C float build
```

Build flags: `-std=c99 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wshadow`. The code
builds clean with both gcc and clang.

## Verification

| check | result |
|---|---|
| **Equivalence, double build vs Python reference.** 54,000 recorded policy calls from the 4-phase simulator, AF-C and AF-T, 3 seeds, replayed open-loop | max \|diff\| **7.3e-15 mm** |
| **Equivalence, float build vs Python reference.** Same calls | max \|diff\| **5.2e-6 mm**, no call off by more than 1e-3 mm |
| **ASan + UBSan**, 20 M ticks | clean |
| **Closed loop.** Simulator driven by the C float build vs the Python reference | AF-C: identical to ±0.04 points (4 seeds). P3 AF-T: −0.13 ± 0.12 points over 50 seeds, i.e. no detectable offset |

Closed loop, a 1e-6 mm difference can eventually flip one discrete decision (gate,
confirm, sweep), and from then on the two trajectories differ. So per-seed scores move a
little in both directions, even for the double build; compare the means.

How big is that effect? Feeding the *double* build float32-rounded inputs moves P3 AF-T by
+0.22 ± 0.08 points (30 seeds). At that level the result is limited by how sensitive the
closed loop is to tiny perturbations, not by single precision.

## Cost and size

| target | result |
|---|---|
| x86-64, gcc -O2 | **~37 ns per tick**; the Python reference takes ~28 µs |
| ARM Cortex-M4F, FPv4-SP hard-float, -Os | **.text 1300 bytes**; hardware FPU (`vadd.f32`, `vmul.f32`); needs only `powf` and `memset` |
| ARM Cortex-A53, -O2 | **.text 1396 bytes**; needs only `powf` |

The ARM objects were built with `zig cc` (`pip install ziglang`):

```bash
python -m ziglang cc -target thumb-linux-musleabihf -mcpu=cortex_m4+vfp4d16sp -mfloat-abi=hard -Os \
    -fno-unwind-tables -fno-asynchronous-unwind-tables -std=c99 -c af_dual_gated.c
python -m ziglang cc -target aarch64-linux-musl -mcpu=cortex_a53 -O2 \
    -fno-unwind-tables -fno-asynchronous-unwind-tables -std=c99 -c af_dual_gated.c
```

**Gotcha found on the way.** `-mcpu=cortex_m4` alone compiled to *software* floating point,
so every add and multiply became an `__aeabi_f*` library call. The FPU feature has to be
named explicitly (`+vfp4d16sp`). Always check the disassembly.

## Notes for a real target

- **MCU without a fast `powf`.** Replace σ(c) with a small calibration table. Cameras store
  these per lens and aperture anyway.
- **Single-precision covariance.** A single-precision Kalman covariance can lose
  positive-definiteness after a re-init with a wide prior, because R/S becomes tiny. The
  update includes a cheap guard. It fired 0 times in 108,000 updates in testing, in both
  builds (counted with a `-DAF_COUNT_PD_FIXES` build), so it does not change the validated
  behaviour.
- **No-FPU targets.** Fixed point (Q16.16) would be the next step for a DSP or a
  lens-controller MCU without an FPU.
