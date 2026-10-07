/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/*
 * af_chain.h -- the whole AF loop in one call per frame:
 *
 *   PDAF views --af_phase--> per-zone (disparity, confidence)
 *              --af_track (AF-T only)--> subject zones --combine--> one measurement
 *              --af_dual_gated--> lens command
 *
 * AF-C combines every zone (follow what fills the AF area); AF-T lets the tracker pick the
 * subject's zones and coasts the brain while the subject is hidden. One af_chain per AF
 * instance; no heap.
 */
#ifndef AF_CHAIN_H
#define AF_CHAIN_H

#include "af_phase.h"
#include "af_track.h"
#include "af_dual_gated.h"

enum { AF_MODE_C = 0, AF_MODE_T = 1 };

typedef struct {
    int mode, height, n_zones;
    af_real px_per_mm;                 /* PDAF disparity (px) per mm of lens defocus */
    af_pc pc;
    af_track tr;
    af_state brain;
    af_real disp[AF_MAX_ZONES], conf[AF_MAX_ZONES], z[AF_MAX_ZONES];
    unsigned char mask[AF_MAX_ZONES];
    int held;                          /* 1 if the last frame was "subject hidden" */
} af_chain;

unsigned long af_chain_sizeof(void);
/* width x height PDAF views split into n_zones horizontal bands. 0 on success. */
int     af_chain_init(af_chain *ch, int mode, int width, int height, int n_zones,
                      int max_disp_px, af_real px_per_mm);
/* One frame of views (row-major, width floats per row) exposed at lens position `lens`. */
af_real af_chain_frame(af_chain *ch, const float *L, const float *R, af_real lens);
/* One frame of per-zone results from a hardware PDAF block (most real sensors / ISPs deliver
 * these instead of raw L/R views): disparity in px and confidence for each of the n_zones
 * zones, exposed at lens position `lens`. Skips af_phase; everything after it is identical. */
af_real af_chain_frame_zones(af_chain *ch, const af_real *disp_px, const af_real *conf, af_real lens);
/* No views this frame (dropout, pipeline warm-up). */
af_real af_chain_coast(af_chain *ch);

#endif /* AF_CHAIN_H */
