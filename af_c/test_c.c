/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* test_c.c -- pure-C unit tests for the AF chain (make check). No Python needed. */
#define _POSIX_C_SOURCE 199309L
#include "af_chain.h"
#include <stdio.h>
#include <stdint.h>
#include <math.h>

static int fails = 0, passes = 0;
#define CHECK(cond, ...) do { if (cond) passes++; else { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                              printf(__VA_ARGS__); printf("\n"); } } while (0)

static uint64_t rs = 0x2545F4914F6CDD1Dull;
static double urand(void)
{
    rs ^= rs >> 12; rs ^= rs << 25; rs ^= rs >> 27;
    return (double)((rs * 2685821657736338717ull) >> 11) * (1.0 / 9007199254740992.0);
}

#define W 256
#define H 64
static float Lv[H * W], Rv[H * W];
static double prof[W + 64];

/* smooth random 1-D texture */
static void make_profile(void)
{
    int i; double v = 0;
    for (i = 0; i < W + 64; i++) { v = 0.8 * v + (urand() - 0.5); prof[i] = v; }
}

/* fill rows [r0, r1) of L and R with the profile, R shifted right by s pixels (linear interp) */
static void fill(int r0, int r1, double s)
{
    int r, k;
    for (r = r0; r < r1; r++)
        for (k = 0; k < W; k++) {
            double t = (double)k + 32.0 - s; int i0 = (int)t; double f = t - i0;
            Lv[r * W + k] = (float)prof[k + 32];
            Rv[r * W + k] = (float)((1 - f) * prof[i0] + f * prof[i0 + 1]);
        }
}

static void test_phase(void)
{
    af_pc pc; af_real d, c, dz[14], cz[14], dm, cm;
    double shifts[] = {0.0, 3.0, -3.0, 7.25, -11.5, 0.4};
    int i, z;
    CHECK(af_pc_init(&pc, W, 48) == 0, "af_pc_init");
    CHECK(af_pc_init(&pc, 1000, 48) != 0, "af_pc_init must reject width > AF_PC_MAX_N");
    af_pc_init(&pc, W, 48);
    make_profile();
    for (i = 0; i < 6; i++) {             /* R = L shifted right by s  ->  disparity = -s */
        fill(0, 4, shifts[i]);
        af_pc_strip(&pc, Lv, Rv, 4, W, &d, &c);
        CHECK(AF_FABS(d + (af_real)shifts[i]) < 0.15, "shift %+.2f: disparity %+.3f (want %+.3f)",
              shifts[i], (double)d, -shifts[i]);
        CHECK(c > 0.5, "shift %+.2f: confidence %.3f should be high", shifts[i], (double)c);
    }
    /* zones: top 5 zones shifted by 10 px (an "occluder"), the rest by 2 px */
    fill(0, 20, 10.0); fill(20, H, 2.0);
    af_pc_zones(&pc, Lv, Rv, H, 14, dz, cz);
    for (z = 0; z < 14; z++) {
        double want = z < 5 ? -10.0 : -2.0;
        CHECK(AF_FABS(dz[z] - (af_real)want) < 0.2, "zone %d: %+.3f want %+.1f", z, (double)dz[z], want);
    }
    af_pc_combine(dz, cz, 0, 14, &dm, &cm);   /* median over 14 zones: 9 at -2, 5 at -10 */
    CHECK(AF_FABS(dm + 2) < 0.2, "combined median %+.3f want -2", (double)dm);
    {
        unsigned char m[14] = {1,1,1,1,1,0,0,0,0,0,0,0,0,0};
        af_pc_combine(dz, cz, m, 14, &dm, &cm);
        CHECK(AF_FABS(dm + 10) < 0.2, "masked median %+.3f want -10", (double)dm);
    }
}

static void test_combine(void)
{
    af_real d[3] = {0.1f, 0.2f, 0.3f}, c[3] = {0.5f, 0.5f, 0.5f}, dm, cm, sd, agree, want;
    af_pc_combine(d, c, 0, 3, &dm, &cm);
    sd = AF_SQRT(((af_real)0.01 + 0 + (af_real)0.01) / 3);
    agree = AF_EXP(-sd / 2);
    want = (af_real)0.5 * agree + (af_real)0.25 * (1 - (af_real)0.2);     /* near-focus bonus */
    CHECK(AF_FABS(dm - (af_real)0.2) < 1e-6, "median %.6f", (double)dm);
    CHECK(AF_FABS(cm - want) < 1e-5, "composite confidence %.6f want %.6f", (double)cm, (double)want);
}

static void test_brain(void)
{
    af_params p; af_state st; af_real x = -1, v = -1, cmd;
    int i;
    af_default_params(&p); af_init(&st, &p);
    CHECK(af_predict(&st, &x, &v) == 0 && x == -1, "af_predict before the first track must return 0");
    for (i = 0; i < 60; i++) cmd = af_step(&st, (af_real)0.0, (af_real)0.9, (af_real)1.5);
    CHECK(AF_FABS(cmd - (af_real)1.5) < 1e-3, "static subject: command %.4f want 1.5", (double)cmd);
    CHECK(af_predict(&st, &x, &v) == 1 && AF_FABS(x - (af_real)1.5) < 1e-3 && v > 0 && v < 0.01,
          "af_predict %.4f var %.6f", (double)x, (double)v);
    /* a confident jump is accepted only after n_confirm = 2 consecutive measurements */
    cmd = af_step(&st, (af_real)-2.0, (af_real)0.9, (af_real)1.5);
    CHECK(AF_FABS(cmd - (af_real)1.5) < 0.05, "first outlier must be gated: %.3f", (double)cmd);
    cmd = af_step(&st, (af_real)-2.0, (af_real)0.9, (af_real)1.5);
    CHECK(AF_FABS(cmd - (af_real)3.5) < 0.05, "second same-sign outlier = real step: %.3f", (double)cmd);
}

static void test_tracker(void)
{
    af_track_params tp; af_track t; unsigned char m[14];
    af_real z[14], c[14], x = (af_real)1.05, v = (af_real)0.0036;
    int i, f, n, r;
    af_track_default_params(&tp); af_track_init(&t, &tp);
    for (i = 0; i < 14; i++) { z[i] = x; c[i] = (af_real)0.52; }
    r = af_track_select(&t, z, c, 14, 1, x, v, m);
    for (n = 0, i = 0; i < 14; i++) n += m[i];
    CHECK(r == 1 && n == 14, "all zones on the subject: %d zones", n);
    for (i = 0; i < 5; i++) z[i] = (af_real)2.62;               /* occluder slides in from zone 0 */
    for (f = 0; f < tp.occ_confirm; f++) r = af_track_select(&t, z, c, 14, 1, x, v, m);
    for (n = 0, i = 0; i < 14; i++) n += m[i];
    CHECK(r == 1 && n == 9 && t.occ_life > 0, "partial cover: %d subject zones, occluder track %d", n, t.occ_life);
    for (i = 0; i < 14; i++) z[i] = (af_real)2.62;              /* fully covered */
    r = af_track_select(&t, z, c, 14, 1, x, (af_real)4.0, m);   /* even with a huge subject variance */
    CHECK(r == 0, "fully covered: subject must be held (got %d)", r);
    for (i = 10; i < 14; i++) z[i] = (af_real)1.08;             /* occluder leaving */
    r = af_track_select(&t, z, c, 14, 1, x, (af_real)1.0, m);
    for (n = 0, i = 0; i < 14; i++) n += m[i];
    CHECK(r == 1 && n == 4, "occluder leaving: %d subject zones (want 4)", n);
    af_track_init(&t, &tp);                                      /* sudden jump, no occluder seen */
    for (i = 0; i < 14; i++) { z[i] = (af_real)2.62; c[i] = (af_real)0.9; }
    r = af_track_select(&t, z, c, 14, 1, x, v, m);
    for (n = 0, i = 0; i < 14; i++) n += m[i];
    CHECK(r == 1 && n == 14, "sudden whole-area change = subject moved: re-acquire (%d zones)", n);
    af_track_init(&t, &tp);                                      /* scattered "rain" never arms */
    for (i = 0; i < 14; i++) { z[i] = x; c[i] = (af_real)0.9; }
    for (f = 0; f < 20; f++) {
        for (i = 0; i < 14; i++) z[i] = ((i + f) % 3 == 0 && i != 0 && i != 13) ? (af_real)2.1 : x;
        af_track_select(&t, z, c, 14, 1, x, v, m);
    }
    CHECK(t.occ_life == 0, "scattered nearer zones must not start an occluder track");
}

static void test_chain_zones(void)
{
    /* af_chain_frame_zones fed the eyes' per-zone output must equal af_chain_frame */
    static af_chain a, b; static af_pc pc;
    af_real d[14], c[14], ca, cb, worst = 0;
    int f;
    make_profile();
    af_chain_init(&a, AF_MODE_T, W, H, 14, 48, (af_real)1.7185);
    af_chain_init(&b, AF_MODE_T, W, H, 14, 48, (af_real)1.7185);
    af_pc_init(&pc, W, 48);
    for (f = 0; f < 120; f++) {
        double s = 2.0 * sin(f * 0.07) + (f > 60 && f < 80 ? 6.0 : 0.0);  /* motion + a jump */
        fill(0, f > 60 && f < 80 ? 20 : 0, s + 5.0); fill(f > 60 && f < 80 ? 20 : 0, H, s);
        af_pc_zones(&pc, Lv, Rv, H, 14, d, c);
        ca = af_chain_frame(&a, Lv, Rv, (af_real)1.2);
        cb = af_chain_frame_zones(&b, d, c, (af_real)1.2);
        if (AF_FABS(ca - cb) > worst) worst = AF_FABS(ca - cb);
    }
    CHECK(worst == 0, "af_chain_frame_zones must equal af_chain_frame on the same zones (worst %.3g)", (double)worst);
}

int main(void)
{
    test_phase(); test_combine(); test_brain(); test_tracker(); test_chain_zones();
    printf("%s: %d checks passed, %d failed (%s build)\n", fails ? "FAIL" : "OK", passes, fails,
           sizeof(af_real) == 8 ? "double" : "float");
    return fails ? 1 : 0;
}
