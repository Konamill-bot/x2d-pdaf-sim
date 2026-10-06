/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* af_track.c -- see af_track.h. Python reference: pdaf_sim/zone_tracker.py::ZoneTracker. */
#include "af_track.h"

unsigned long af_track_sizeof(void) { return (unsigned long)sizeof(af_track); }

void af_track_default_params(af_track_params *prm)
{
    prm->s0 = (af_real)0.031; prm->p = (af_real)1.58;
    prm->c_floor = (af_real)0.05; prm->gate = (af_real)3.5;
    prm->m_min = 2; prm->k_occ = 2; prm->occ_tol = (af_real)3.0;
    prm->occ_confirm = 4; prm->occ_window = 30; prm->max_hold = 150;
}

void af_track_init(af_track *t, const af_track_params *prm)
{
    t->prm = *prm;
    t->occ_z = 0; t->occ_var = 0; t->occ_life = 0;
    t->cand_z = 0; t->cand_sig = 0; t->cand_n = 0; t->cand_side = 0;
    t->hold = 0;
}

static int count(const unsigned char *m, int n)
{
    int i, c = 0;
    for (i = 0; i < n; i++) c += m[i] != 0;
    return c;
}

/* gather v[i] for mask[i] into out; returns how many */
static int gather(const af_real *v, const unsigned char *m, int n, af_real *out)
{
    int i, k = 0;
    for (i = 0; i < n; i++) if (m[i]) out[k++] = v[i];
    return k;
}

/* keep only the contiguous run of m that starts at one end (side 0: first zone, 1: last) */
static void end_run(unsigned char *m, int n, int side)
{
    int i, on = 1;
    for (i = 0; i < n; i++) {
        int k = side ? n - 1 - i : i;
        if (!m[k]) on = 0;
        m[k] = (unsigned char)(on && m[k]);
    }
}

/* keep only the longest contiguous run of m touching either end (ties: the front) */
static void edge_run(unsigned char *m, int n)
{
    int a = 0, b = n, i;
    while (a < n && m[a]) a++;
    while (b > 0 && m[b - 1]) b--;
    for (i = 0; i < n; i++) m[i] = (a >= n - b) ? (unsigned char)(i < a) : (unsigned char)(i >= b);
}

static void set_occluder(af_track *t, const af_real *z, const af_real *sig, const unsigned char *m, int n)
{
    af_real zz[AF_MAX_ZONES], ss[AF_MAX_ZONES], buf[AF_MAX_ZONES], sm;
    int k = gather(z, m, n, zz);
    gather(sig, m, n, ss);
    t->occ_z = af_median(zz, k, buf);
    sm = af_median(ss, k, buf);
    t->occ_var = sm * sm / (af_real)k;
    t->occ_life = t->prm.occ_window;
}

int af_track_select(af_track *t, const af_real *z, const af_real *conf, int n,
                    int has_pred, af_real x, af_real var, unsigned char *mask)
{
    const af_track_params *p = &t->prm;
    af_real sig[AF_MAX_ZONES], g2 = p->gate * p->gate;
    unsigned char valid[AF_MAX_ZONES], subj[AF_MAX_ZONES], occ[AF_MAX_ZONES], nearer[AF_MAX_ZONES];
    int i;
    for (i = 0; i < n; i++) {
        valid[i] = conf[i] >= p->c_floor;
        sig[i] = p->s0 * AF_POW(conf[i] > p->c_floor ? conf[i] : p->c_floor, -p->p);
    }
    if (!has_pred) {
        t->occ_life = 0; t->hold = 0; t->cand_n = 0;
        for (i = 0; i < n; i++) mask[i] = valid[i];
        return 1;
    }
    for (i = 0; i < n; i++) {
        af_real d = z[i] - x;
        subj[i] = valid[i] && d * d <= g2 * (var + sig[i] * sig[i]);
        occ[i] = 0;
    }
    if (t->occ_life > 0) {                       /* explain away: the known occluder first, */
        for (i = 0; i < n; i++) {                /* but only in front of the subject        */
            af_real d = z[i] - t->occ_z;
            occ[i] = valid[i] && d * d <= g2 * (t->occ_var + sig[i] * sig[i]) && z[i] > x;
            if (occ[i]) subj[i] = 0;
        }
        t->occ_life--;
    }
    if (count(subj, n) >= p->m_min) {
        for (i = 0; i < n; i++) nearer[i] = valid[i] && !subj[i] && z[i] > x;
        edge_run(nearer, n);
        if (count(nearer, n) < p->k_occ) {
            /* An occluder only slightly nearer than the subject falls inside the subject gate
             * (as wide as the PREDICTION's uncertainty). Segment within the frame instead: an
             * edge block of subject zones clearly nearer than the rest is a nearer object. */
            af_real zs[AF_MAX_ZONES], ss[AF_MAX_ZONES], buf[AF_MAX_ZONES], med, sg, best_gap = -1;
            unsigned char blk[AF_MAX_ZONES], rest[AF_MAX_ZONES], bb[AF_MAX_ZONES], br[AF_MAX_ZONES];
            int k = gather(z, subj, n, zs), side;
            gather(sig, subj, n, ss);
            med = af_median(zs, k, buf); sg = af_median(ss, k, buf);
            for (side = 0; side < 2; side++) {
                int e = side ? n - 1 : 0, nb, nr;
                af_real thr, gap, zb[AF_MAX_ZONES], zr[AF_MAX_ZONES];
                if (!subj[e] || z[e] <= med) continue;
                thr = (af_real)0.5 * (z[e] + med);
                for (i = 0; i < n; i++) blk[i] = subj[i] && z[i] > thr;
                end_run(blk, n, side);
                for (i = 0; i < n; i++) rest[i] = subj[i] && !blk[i];
                nb = gather(z, blk, n, zb); nr = gather(z, rest, n, zr);
                if (nb < p->k_occ || nr < p->m_min) continue;
                gap = af_median(zb, nb, buf) - af_median(zr, nr, buf);
                if (gap > p->occ_tol * sg && gap > best_gap) {
                    best_gap = gap;
                    for (i = 0; i < n; i++) { bb[i] = blk[i]; br[i] = rest[i]; }
                }
            }
            if (best_gap > 0)
                for (i = 0; i < n; i++) { nearer[i] = bb[i]; subj[i] = br[i]; }
        }
        if (count(occ, n) >= 1) {
            set_occluder(t, z, sig, occ, n);
        } else if (count(nearer, n) >= p->k_occ) {
            af_real zn[AF_MAX_ZONES], sn[AF_MAX_ZONES], buf[AF_MAX_ZONES], zmin, zmax, sm, zc;
            int k = gather(z, nearer, n, zn);
            gather(sig, nearer, n, sn);
            sm = af_median(sn, k, buf); zc = af_median(zn, k, buf);
            zmin = zmax = zn[0];
            for (i = 1; i < k; i++) { if (zn[i] < zmin) zmin = zn[i]; if (zn[i] > zmax) zmax = zn[i]; }
            if (zmax - zmin <= p->occ_tol * sm) {
                af_real sz = sm / AF_SQRT((af_real)k), tol;
                int side = nearer[0] ? 0 : 1, same;
                tol = p->occ_tol * (sz > t->cand_sig ? sz : t->cand_sig);
                same = t->cand_n > 0 && side == t->cand_side && AF_FABS(zc - t->cand_z) <= tol;
                t->cand_n = same ? t->cand_n + 1 : 1;
                t->cand_z = zc; t->cand_sig = sz; t->cand_side = side;
                if (t->cand_n >= p->occ_confirm) { set_occluder(t, z, sig, nearer, n); t->cand_n = 0; }
            } else {
                t->cand_n = 0;
            }
        } else {
            t->cand_n = 0;
        }
        t->hold = 0;
        for (i = 0; i < n; i++) mask[i] = subj[i];
        return 1;
    }
    if (t->occ_life > 0 && t->hold < p->max_hold) {   /* subject hidden behind the occluder */
        if (count(occ, n) >= 1) t->occ_life = p->occ_window;   /* depth frozen while hidden */
        t->hold++;
        return 0;
    }
    t->hold = 0; t->occ_life = 0; t->cand_n = 0;      /* subject moved: re-acquire */
    for (i = 0; i < n; i++) mask[i] = valid[i];
    return 1;
}
