/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* af_dual_gated.c -- see af_dual_gated.h. Mirrors pdaf_sim/policy_dual.py::DualGated,
 * the Python reference model (af_c/test_equiv.py checks the two against each other). */
#include "af_dual_gated.h"

#ifdef AF_COUNT_PD_FIXES
long af_pd_fixes = 0;                  /* test hook: how often the PD guard fired */
#define AF_PD_FIXED() (af_pd_fixes++)
#else
#define AF_PD_FIXED() ((void)0)
#endif

void af_default_params(af_params *prm)
{
    prm->s0 = (af_real)0.036;  prm->p = (af_real)0.93;
    prm->c_floor = (af_real)0.05;  prm->gate = (af_real)3.5;  prm->n_confirm = 2;
    prm->q_hi = (af_real)1e-3;  prm->q_lo = (af_real)1e-5;
    prm->lead = (af_real)3.0;  prm->pv0 = (af_real)(0.05 * 0.05);
    prm->sweep_step = (af_real)0.3;  prm->lo = (af_real)0.0;  prm->hi = (af_real)7.0;
    prm->handover_pos = 1;
}

void af_init(af_state *st, const af_params *prm)
{
    af_kf zero = {0, 0, 0, 0, 0};
    st->prm = *prm;
    st->a = zero; st->s = zero;
    st->initialized = 0; st->coasting = 0;
    st->low = 0; st->dir = 1; st->nrej = 0; st->rsign = 0;
}

/* x <- F x,  P <- F P F^T + q [[1/3 1/2] [1/2 1]],  F = [[1 1] [0 1]] */
static void kf_predict(af_kf *k, af_real q)
{
    af_real p00 = k->p00 + 2 * k->p01 + k->p11 + q / 3;
    af_real p01 = k->p01 + k->p11 + q / 2;
    af_real p11 = k->p11 + q;
    k->x0 += k->x1;
    k->p00 = p00; k->p01 = p01; k->p11 = p11;
}

/* scalar position measurement z with variance R (H = [1 0]) */
static void kf_update(af_kf *k, af_real z, af_real R)
{
    af_real S = k->p00 + R;
    af_real k0 = k->p00 / S, k1 = k->p01 / S;
    af_real nu = z - k->x0;
    af_real p00 = k->p00 - k0 * k->p00;
    af_real p01 = k->p01 - k0 * k->p01;
    af_real p11 = k->p11 - k1 * k->p01;
    k->x0 += k0 * nu; k->x1 += k1 * nu;
    /* Guard: keep P positive definite. Exact arithmetic gives det(P+) = det(P) * R / S > 0,
     * but in single precision a tiny R/S (e.g. right after a re-init with a wide prior) can
     * round det(P+) to <= 0. Never fires in double precision on the validation runs. */
    if (p00 * p11 - p01 * p01 <= 0) {
        af_real lim = AF_SQRT(p00 * p11) * (af_real)0.999;
        p01 = p01 > 0 ? lim : -lim;
        AF_PD_FIXED();
    }
    k->p00 = p00; k->p01 = p01; k->p11 = p11;
}

static af_real clampr(af_real v, af_real lo, af_real hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

static af_real cmd(const af_state *st)
{
    return clampr(st->a.x0 + st->a.x1 * st->prm.lead, st->prm.lo, st->prm.hi);
}

static void track_init(af_state *st, af_real z, af_real R)
{
    af_kf k;
    k.x0 = z; k.x1 = 0; k.p00 = R; k.p01 = 0; k.p11 = st->prm.pv0;
    st->a = k; st->s = k;
    st->nrej = 0; st->coasting = 0; st->initialized = 1;
}

static void predict_both(af_state *st)
{
    kf_predict(&st->a, st->prm.q_hi);
    kf_predict(&st->s, st->prm.q_lo);
}

static void enter_coast(af_state *st)
{
    if (st->coasting) return;
    if (st->prm.handover_pos) {
        st->a = st->s;                       /* long-horizon position and velocity */
    } else {
        st->a.x1 = st->s.x1;                 /* long-horizon velocity only */
        st->a.p11 = st->s.p11; st->a.p01 = 0;
    }
    st->coasting = 1;
}

static af_real sweep(af_state *st, af_real lens)
{
    af_real c = lens + (af_real)st->dir * st->prm.sweep_step;
    if (c > st->prm.hi || c < st->prm.lo) {
        st->dir = -st->dir;
        c = lens + (af_real)st->dir * st->prm.sweep_step;
    }
    track_init(st, c, (af_real)4.0);
    return c;
}

af_real af_step(af_state *st, af_real d, af_real c, af_real lens)
{
    const af_params *p = &st->prm;
    af_real z = lens - d;
    af_real sig = p->s0 * AF_POW(c > p->c_floor ? c : p->c_floor, -p->p);
    af_real R = sig * sig;
    af_real nu, S;

    if (!st->initialized) {
        if (c < p->c_floor) return lens;
        track_init(st, z, R);
        return cmd(st);
    }
    if (c < p->c_floor) {                    /* no usable measurement: coast */
        enter_coast(st);
        predict_both(st);
        st->low++;
        if (st->low >= 4 && st->a.p00 > (af_real)1.0) return sweep(st, lens);
        return cmd(st);
    }
    predict_both(st);
    st->low = 0;
    nu = z - st->a.x0;
    S = st->a.p00 + R;
    if (nu * nu > p->gate * p->gate * S) {   /* gated out: keep the track */
        int s = nu > 0 ? 1 : -1;
        st->nrej = (s == st->rsign) ? st->nrej + 1 : 1;
        st->rsign = s;
        if (st->nrej >= p->n_confirm) track_init(st, z, R);   /* persistent => real step */
        return cmd(st);
    }
    st->nrej = 0; st->coasting = 0;
    kf_update(&st->a, z, R);
    kf_update(&st->s, z, R);
    return cmd(st);
}

af_real af_coast(af_state *st)
{
    if (!st->initialized) return 0;
    enter_coast(st);
    predict_both(st);
    return cmd(st);
}

int af_predict(const af_state *st, af_real *x, af_real *var)
{
    const af_kf *k = st->coasting ? &st->s : &st->a;
    af_real q = st->coasting ? st->prm.q_lo : st->prm.q_hi;
    if (!st->initialized) return 0;
    *x = k->x0 + k->x1;
    *var = k->p00 + 2 * k->p01 + k->p11 + q / 3;
    return 1;
}
