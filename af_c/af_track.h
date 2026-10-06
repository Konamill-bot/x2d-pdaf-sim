/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/*
 * af_track.h -- depth-only AF-T subject tracker (which AF zones are the subject).
 *
 * Mirrors pdaf_sim/zone_tracker.py::ZoneTracker. Input per measurement: each zone's
 * in-focus lens position z (mm) and confidence, plus the AF brain's predicted subject
 * position and variance (af_predict). Output: a mask of subject zones, or "hidden" (the
 * brain should coast).
 *
 *   subject zones  : within GATE sigmas of the predicted subject position
 *   occluder track : a second object, started when a nearer, self-consistent block of
 *                    zones at an edge of the AF area appears next to visible subject
 *                    zones and persists (same edge, same depth) for OCC_CONFIRM frames;
 *                    zones it explains (in front of the subject) are never the subject
 *   subject hidden : fewer than M_MIN subject zones while the occluder track lives:
 *                    hold for up to MAX_HOLD steps (occluder depth frozen meanwhile)
 *   subject moved  : every zone changed at once with no occluder seen: re-acquire
 */
#ifndef AF_TRACK_H
#define AF_TRACK_H

#include "af_util.h"

typedef struct {
    af_real s0, p;        /* per-zone noise: sigma(c) = s0 * c^-p (mm)           */
    af_real c_floor;      /* zones below this confidence are ignored             */
    af_real gate;         /* association gate (sigmas)                           */
    int     m_min;        /* subject zones needed to call the subject visible    */
    int     k_occ;        /* zones needed to start an occluder candidate         */
    af_real occ_tol;      /* candidate zones agree within occ_tol sigmas         */
    int     occ_confirm;  /* frames a candidate must persist                     */
    int     occ_window;   /* steps an unseen occluder track survives             */
    int     max_hold;     /* steps to hold through full occlusion                */
} af_track_params;

typedef struct {
    af_track_params prm;
    af_real occ_z, occ_var;    int occ_life;
    af_real cand_z, cand_sig;  int cand_n, cand_side;
    int hold;
} af_track;

unsigned long af_track_sizeof(void);
void af_track_default_params(af_track_params *prm);
void af_track_init(af_track *t, const af_track_params *prm);

/* has_pred = 0 before the brain has a track (then x, var are ignored).
 * Returns 1 and fills mask[0..n) with the zones to measure from, or returns 0 when the
 * subject is hidden (call af_coast on the brain). n <= AF_MAX_ZONES. */
int af_track_select(af_track *t, const af_real *z_mm, const af_real *conf, int n,
                    int has_pred, af_real x, af_real var, unsigned char *mask);

#endif /* AF_TRACK_H */
