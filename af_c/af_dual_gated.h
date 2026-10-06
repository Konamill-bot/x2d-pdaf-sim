/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/*
 * af_dual_gated.h -- two-timescale, confidence-gated Kalman AF-C decision policy.
 *
 * C99, no heap, no global state, fixed work per frame. One af_state per AF instance.
 *
 * Per AF-loop tick the host calls exactly one of:
 *   af_step(st, d, c, lens)  -- a PDAF measurement arrived: d = defocus (mm, lens-plane),
 *                               c = PSR confidence in [0,1], lens = lens position (mm) at the
 *                               time the measurement was exposed (timestamp-correct)
 *   af_coast(st)             -- no measurement this tick (pipeline warm-up, dropout,
 *                               tracker says the subject is hidden)
 * Both return the lens position command (mm), clamped to [lo, hi].
 *
 * Define AF_REAL_DOUBLE to build in double precision (used for the equivalence test
 * against the Python reference model); the default is float.
 *
 */
#ifndef AF_DUAL_GATED_H
#define AF_DUAL_GATED_H

#include "af_util.h"

typedef struct {
    af_real s0;          /* measurement noise calibration: sigma(c) = s0 * c^-p  (mm) */
    af_real p;
    af_real c_floor;     /* confidence below this = no usable measurement              */
    af_real gate;        /* innovation gate, in standard deviations                    */
    int     n_confirm;   /* consecutive same-sign gated-out measurements => real step  */
    af_real q_hi;        /* white-acceleration noise, agile filter  (mm^2 / tick^3)    */
    af_real q_lo;        /* white-acceleration noise, smooth filter (mm^2 / tick^3)    */
    af_real lead;        /* prediction lead (ticks): pipeline latency                  */
    af_real pv0;         /* initial velocity variance ((mm/tick)^2)                    */
    af_real sweep_step;  /* search step when the track is lost (mm)                    */
    af_real lo, hi;      /* lens travel limits (mm)                                    */
    int     handover_pos;/* on coast entry take smooth position too (1) or velocity only (0) */
} af_params;

typedef struct {
    af_real x0, x1;          /* position (mm), velocity (mm/tick) */
    af_real p00, p01, p11;   /* symmetric 2x2 covariance          */
} af_kf;

typedef struct {
    af_params prm;
    af_kf a;                 /* agile filter: drives the lens while measurements arrive */
    af_kf s;                 /* smooth filter: long-horizon estimate used for coasting   */
    int initialized, coasting, low, dir, nrej, rsign;
} af_state;

void    af_default_params(af_params *prm);
void    af_init(af_state *st, const af_params *prm);
af_real af_step(af_state *st, af_real d, af_real c, af_real lens);
af_real af_coast(af_state *st);

/* Where the subject should be at the next measurement: position (mm) and variance, without
 * changing any state (the smooth filter while coasting, else the agile one). Returns 0 and
 * leaves x, var untouched before the first track. */
int     af_predict(const af_state *st, af_real *x, af_real *var);

#endif /* AF_DUAL_GATED_H */
