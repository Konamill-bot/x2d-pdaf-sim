/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* bench.c -- per-tick cost of af_step/af_coast on a synthetic AF-C input stream. */
#define _POSIX_C_SOURCE 199309L
#include "af_dual_gated.h"
#include <stdio.h>
#include <stdint.h>
#include <time.h>

static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static double urand(void)                     /* xorshift64*, [0,1) */
{
    rng_state ^= rng_state >> 12; rng_state ^= rng_state << 25; rng_state ^= rng_state >> 27;
    return (double)((rng_state * 2685821657736338717ull) >> 11) * (1.0 / 9007199254740992.0);
}

int main(void)
{
    enum { N = 20000000 };
    af_params prm; af_state st;
    double tgt = 1.2, vel = 0.0, lens = 1.2, sink = 0.0;
    struct timespec t0, t1;
    long i;
    af_default_params(&prm);
    af_init(&st, &prm);
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (i = 0; i < N; i++) {
        double c, d, cmdv;
        vel = 0.97 * vel + (urand() - 0.5) * 0.004;           /* subject wander (mm/tick) */
        tgt += vel;
        if (tgt < 0.2 || tgt > 6.5) vel = -vel;
        c = urand();
        d = (lens - tgt) + (urand() - 0.5) * 0.1;              /* noisy defocus */
        if (c < 0.03)
            cmdv = (double)af_coast(&st);                      /* ~3% dropouts */
        else
            cmdv = (double)af_step(&st, (af_real)d, (af_real)c, (af_real)lens);
        lens += 0.5 * (cmdv - lens);                           /* crude actuator */
        sink += cmdv;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    {
        double ns = ((double)(t1.tv_sec - t0.tv_sec) * 1e9 + (double)(t1.tv_nsec - t0.tv_nsec)) / N;
        printf("af_step/af_coast: %.1f ns per tick (%d ticks), state = %zu bytes, sink=%.3f\n",
               ns, N, sizeof(af_state), sink / N);
    }
    return 0;
}
