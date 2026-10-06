/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* bench.c -- cost of each AF stage per frame: eyes (14-zone phase correlation on 64 x 256
 * PDAF views), tracker, brain, and the whole chain. */
#define _POSIX_C_SOURCE 199309L
#include "af_chain.h"
#include <stdio.h>
#include <stdint.h>
#include <time.h>

#define W 256
#define H 64
#define NZ 14

static uint64_t rs = 0x9E3779B97F4A7C15ull;
static double urand(void)
{
    rs ^= rs >> 12; rs ^= rs << 25; rs ^= rs >> 27;
    return (double)((rs * 2685821657736338717ull) >> 11) * (1.0 / 9007199254740992.0);
}
static double now_ns(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec * 1e9 + (double)t.tv_nsec;
}

static float Lv[H * W], Rv[H * W];

int main(void)
{
    static af_pc pc; static af_chain ch;
    af_params prm; af_state st; af_track_params tp; af_track tr;
    af_real d[NZ], c[NZ], z[NZ]; unsigned char m[NZ];
    double t0, sink = 0, tgt = 1.2, vel = 0, lens = 1.2, prof[W + 8];
    long i; int k, r;
    for (k = 0; k < W + 8; k++) prof[k] = urand();
    for (r = 0; r < H; r++)
        for (k = 0; k < W; k++) {
            Lv[r * W + k] = (float)(prof[k + 2] + 0.01 * urand());
            Rv[r * W + k] = (float)(prof[k + 4] + 0.01 * urand());   /* 2 px disparity */
        }

    /* brain: synthetic AF-C input stream, ~3% dropouts */
    af_default_params(&prm); af_init(&st, &prm);
    t0 = now_ns();
    for (i = 0; i < 20000000; i++) {
        double cc = urand(), dd, cmd;
        vel = 0.97 * vel + (urand() - 0.5) * 0.004; tgt += vel;
        if (tgt < 0.2 || tgt > 6.5) vel = -vel;
        dd = (lens - tgt) + (urand() - 0.5) * 0.1;
        cmd = cc < 0.03 ? (double)af_coast(&st) : (double)af_step(&st, (af_real)dd, (af_real)cc, (af_real)lens);
        lens += 0.5 * (cmd - lens); sink += cmd;
    }
    printf("brain   (af_step / af_coast)          : %8.1f ns per frame\n", (now_ns() - t0) / 20000000.0);

    af_pc_init(&pc, W, 48);
    t0 = now_ns();
    for (i = 0; i < 4000; i++) { af_pc_zones(&pc, Lv, Rv, H, NZ, d, c); sink += (double)d[0]; }
    printf("eyes    (14 zones, 64 x 256 views)    : %8.1f us per frame\n", (now_ns() - t0) / 4000.0 / 1000.0);

    af_track_default_params(&tp); af_track_init(&tr, &tp);
    t0 = now_ns();
    for (i = 0; i < 2000000; i++) {
        for (k = 0; k < NZ; k++) { z[k] = (af_real)(1.0 + 0.03 * (urand() - 0.5) + (k < 4 && (i / 50) % 4 == 1 ? 0.8 : 0.0));
                                   c[k] = (af_real)(0.6 + 0.4 * urand()); }
        sink += af_track_select(&tr, z, c, NZ, 1, (af_real)1.0, (af_real)0.004, m);
    }
    printf("tracker (af_track_select, 14 zones)   : %8.1f ns per frame\n", (now_ns() - t0) / 2000000.0);

    af_chain_init(&ch, AF_MODE_T, W, H, NZ, 48, (af_real)1.7185);
    t0 = now_ns();
    for (i = 0; i < 4000; i++) sink += (double)af_chain_frame(&ch, Lv, Rv, (af_real)1.2);
    printf("chain   (AF-T: eyes + tracker + brain): %8.1f us per frame  (%.2f%% of a 60 fps frame)\n",
           (now_ns() - t0) / 4000.0 / 1000.0, (now_ns() - t0) / 4000.0 / 1e9 * 60.0 * 100.0);
    printf("state: af_state %zu B, af_track %zu B, af_pc %zu B, af_chain %zu B   (sink %.3g)\n",
           sizeof(af_state), sizeof(af_track), sizeof(af_pc), sizeof(af_chain), sink);
    return 0;
}
