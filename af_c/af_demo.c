/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/*
 * af_demo.c -- the whole AF chain in C, closed loop, with no Python.
 *
 * A miniature world in C: a textured subject swaying in depth, an opaque occluder that
 * slides in from one side of the AF area, covers it, and slides out again; defocus blur
 * and the PDAF left/right shift; a 3-frame pipeline latency; a servo voice-coil lens.
 * The same scene is run twice -- once in AF-C, once in AF-T -- and printed as a timeline.
 *
 * The optics here are a simplified 1-D version of pdaf_sim/dualpixel.py (box blur,
 * linear-interpolation shift); the Python simulator remains the reference for numbers.
 *
 *   make af_demo && ./af_demo
 */
#include "af_chain.h"
#include <stdio.h>
#include <stdint.h>
#include <math.h>

#define W    256
#define H    64
#define NZ   14
#define FPS  60
#define LAT  3
#define PAD  64
#define NFR  (12 * FPS)                 /* 12 s */
#define PPM  1.7185                     /* PDAF disparity, px per mm of defocus      */
#define COC  2.0246                     /* blur-circle radius, px per mm of defocus  */
#define DEADBAND 0.10                   /* in focus: |lens - target| < 0.1 mm        */

static const double PI = 3.14159265358979323846;
static uint64_t rng_state;
static double urand(void)
{
    rng_state ^= rng_state >> 12; rng_state ^= rng_state << 25; rng_state ^= rng_state >> 27;
    return (double)((rng_state * 2685821657736338717ull) >> 11) * (1.0 / 9007199254740992.0);
}
static double gauss(void)
{
    double u = urand(), v = urand();
    return sqrt(-2.0 * log(u + 1e-300)) * cos(2 * PI * v);
}

static double tex_subj[W + 2 * PAD], tex_occ[W + 2 * PAD];
static float  ring[LAT + 1][2][H * W];   /* views in flight through the pipeline */
static double ring_lens[LAT + 1];

static void make_texture(double *t)       /* random bars, like pdaf_sim.scene.high_contrast */
{
    int i, b;
    for (i = 0; i < W + 2 * PAD; i++) t[i] = 0;
    for (b = 0; b < 50; b++) {
        int x = (int)(urand() * (W + 2 * PAD)), w = 2 + (int)(urand() * 6);
        double a = 0.3 + 0.7 * urand();
        for (i = x - w; i < x + w; i++) if (i >= 0 && i < W + 2 * PAD) t[i] += a;
    }
}

/* the world at time t (seconds): subject depth, occluder depth, occluder coverage 0..1 */
static double subject_mm(double t) { return 1.25 + 0.30 * sin(2 * PI * t / 5.0); }
static double occluder_mm(void)    { return 2.60; }                 /* nearer than the subject */
static double coverage(double t)
{
    const double t_in = 3.0, t_out = 4.2, ramp = 0.15;                /* one 1.2 s crossing... */
    const double t2_in = 7.5, t2_out = 7.9;                           /* ...and a quick one    */
    double c1 = 0, c2 = 0;
    if (t >= t_in && t < t_out + ramp) c1 = fmin(1.0, fmin((t - t_in) / ramp, (t_out + ramp - t) / ramp));
    if (t >= t2_in && t < t2_out + ramp) c2 = fmin(1.0, fmin((t - t2_in) / ramp, (t2_out + ramp - t) / ramp));
    return fmax(0.0, fmax(c1, c2));
}

/* render one zone band: texture at depth `depth`, lens at `lens` */
static void render_band(float *L, float *R, int r0, int r1, const double *tex, double lens, double depth)
{
    double d = lens - depth, rad = COC * fabs(d), c = 4 * rad / (3 * PI) * (d < 0 ? -1 : 1);
    double blur[W + 2 * PAD], pre[W + 2 * PAD + 1];
    int hw = rad >= 0.6 ? (int)(rad + 0.5) : 0, i, r, k;
    pre[0] = 0;
    for (i = 0; i < W + 2 * PAD; i++) pre[i + 1] = pre[i] + tex[i];
    for (i = 0; i < W + 2 * PAD; i++) {                               /* box blur */
        int a = i - hw < 0 ? 0 : i - hw, b = i + hw + 1 > W + 2 * PAD ? W + 2 * PAD : i + hw + 1;
        blur[i] = (pre[b] - pre[a]) / (b - a);
    }
    for (r = r0; r < r1; r++)
        for (k = 0; k < W; k++) {
            double tl = k + PAD - c, tr = k + PAD + c;                /* L shifted +c, R shifted -c */
            int il = (int)floor(tl), ir = (int)floor(tr);
            double fl = tl - il, fr = tr - ir;
            L[r * W + k] = (float)((1 - fl) * blur[il] + fl * blur[il + 1] + 0.006 * gauss());
            R[r * W + k] = (float)((1 - fr) * blur[ir] + fr * blur[ir + 1] + 0.006 * gauss());
        }
}

static void render(float *L, float *R, double t, double lens)
{
    int n_occ = (int)ceil(coverage(t) * NZ - 1e-9), z, sh = H / NZ;
    for (z = 0; z < NZ; z++) {
        int r0 = z * sh, r1 = z < NZ - 1 ? r0 + sh : H;
        if (z < n_occ) render_band(L, R, r0, r1, tex_occ, lens, occluder_mm());
        else           render_band(L, R, r0, r1, tex_subj, lens, subject_mm(t));
    }
}

typedef struct { double pos, vel, vmax, accel; } motor;          /* servo voice-coil lens */
static double motor_cmd(motor *m, double target)
{
    const double dt = 1.0 / FPS, tau = 0.030;
    double v_des = (target - m->pos) / tau, dv;
    if (v_des > m->vmax) v_des = m->vmax;
    if (v_des < -m->vmax) v_des = -m->vmax;
    dv = v_des - m->vel;
    if (dv > m->accel * dt) dv = m->accel * dt;
    if (dv < -m->accel * dt) dv = -m->accel * dt;
    m->vel += dv; m->pos += m->vel * dt;
    if (m->pos < 0) m->pos = 0;
    if (m->pos > 7) m->pos = 7;
    return m->pos;
}

static double lens_log[2][NFR];
static int    held_log[NFR];

static void run(int mode)
{
    static af_chain ch;
    motor m;
    int k, head = 0, filled = 0;
    rng_state = 0x9E3779B97F4A7C15ull;
    make_texture(tex_subj); make_texture(tex_occ);
    af_chain_init(&ch, mode, W, H, NZ, 48, (af_real)PPM);
    m.pos = subject_mm(0); m.vel = 0; m.vmax = 10000.0 / 300.0; m.accel = m.vmax / 0.020;
    for (k = 0; k < NFR; k++) {
        double t = (double)k / FPS, cmd;
        render(ring[head][0], ring[head][1], t, m.pos);              /* expose this frame */
        ring_lens[head] = m.pos;
        head = (head + 1) % (LAT + 1);
        if (filled < LAT) { filled++; cmd = (double)af_chain_coast(&ch); }   /* pipeline filling */
        else {                                                       /* frame from LAT ago */
            int old = head;                                          /* oldest slot */
            cmd = (double)af_chain_frame(&ch, ring[old][0], ring[old][1], (af_real)ring_lens[old]);
        }
        lens_log[mode][k] = motor_cmd(&m, cmd);
        if (mode == AF_MODE_T) held_log[k] = ch.held;
    }
}

int main(void)
{
    int k, inC = 0, inT = 0, nC = 0, occC = 0, occT = 0, nOcc = 0;
    run(AF_MODE_C); run(AF_MODE_T);
    printf("AF chain in C, closed loop: 12 s, 60 fps, 3-frame latency. Lens positions in mm.\n");
    printf("AF-C should follow whatever fills the AF area; AF-T should stay on the subject.\n\n");
    printf("  time  subject  occluder | AF-C lens     | AF-T lens\n");
    for (k = 0; k < NFR; k++) {
        double t = (double)k / FPS, cov = coverage(t), s = subject_mm(t);
        double tgtC = cov > 0.5 ? occluder_mm() : s;                 /* AF-C target: the AF area */
        int okC = fabs(lens_log[0][k] - tgtC) < DEADBAND, okT = fabs(lens_log[1][k] - s) < DEADBAND;
        inC += okC; inT += okT; nC++;
        if (cov >= 1.0) { occC += fabs(lens_log[0][k] - occluder_mm()) < DEADBAND; occT += okT; nOcc++; }
        if (k % 12 == 0 || (cov > 0 && k % 4 == 0))
            printf(" %5.2f   %5.2f    %s | %5.2f %-7s | %5.2f %s%s\n", t, s,
                   cov > 0 ? (cov >= 1 ? " FULL  " : "partial") : "   -   ",
                   lens_log[0][k], okC ? "in" : "OUT", lens_log[1][k], okT ? "in" : "OUT",
                   held_log[k] ? "  (holding)" : "");
    }
    printf("\nin focus: AF-C %.1f%% (vs the AF area), AF-T %.1f%% (vs the subject)\n",
           100.0 * inC / nC, 100.0 * inT / nC);
    printf("while the occluder fully covers the AF area: AF-C on the occluder %.1f%%, "
           "AF-T on the subject %.1f%%\n", 100.0 * occC / nOcc, 100.0 * occT / nOcc);
    printf("\nNote: during the first crossing the subject reverses direction while hidden (its depth\n"
           "turns around at t = 3.75 s). AF-T coasts at the last measured velocity, so it drifts\n"
           "off and is OUT near the end of that crossing: extrapolation cannot see a reversal it\n"
           "has no measurement of. The second, shorter crossing is held in focus throughout.\n");
    return 0;
}
