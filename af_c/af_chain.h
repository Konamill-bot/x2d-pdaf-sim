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
 * instance; no heap. AF_MODE_ROI follows a detector's subject box instead: one PDAF window
 * fitted to the box per frame (af_chain_frame_roi).
 */
#ifndef AF_CHAIN_H
#define AF_CHAIN_H

#include "af_phase.h"
#include "af_track.h"
#include "af_dual_gated.h"
#include "af_roi.h"

enum { AF_MODE_C = 0, AF_MODE_T = 1, AF_MODE_ROI = 2 };

typedef struct {
    int mode, width, height, n_zones;
    af_real px_per_mm;                 /* PDAF disparity (px) per mm of lens defocus */
    af_pc pc;
    af_track tr;
    af_state brain;
    af_roi_gate gate;                  /* AF_MODE_ROI: the depth veto on the box's window */
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
/* AF_MODE_ROI: AF-T led by a detector's subject box (af_roi.h). Each frame brings at most one
 * measurement, from one PDAF window fitted to the box. It is used unless its depth disagrees
 * with the track, and then vetoed for at most a few frames (after that the detector wins).
 * af_chain_frame_roi takes the window's result as an ISP's PDAF block delivers it; has = 0
 * means no box this frame (a missed detection): the brain coasts, or holds the lens before
 * it has a track. af_chain_frame_box first measures the window from the views (af_pc_window). */
af_real af_chain_frame_roi(af_chain *ch, int has, af_real disp_px, af_real conf, af_real lens);
af_real af_chain_frame_box(af_chain *ch, const float *L, const float *R, af_win win, af_real lens);
/* No views this frame (dropout, pipeline warm-up). */
af_real af_chain_coast(af_chain *ch);

#endif /* AF_CHAIN_H */
