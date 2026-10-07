/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/*
 * af_roi.h -- subject-box AF: the PDAF window an ISP measures for a detector's box, and the
 * ROI-led depth veto. Mirrors pdaf_sim/roi.py (IspWindow, RoiGate).
 *
 * A detector gives the subject's box a few frames late. The AF then measures phase in ONE
 * window fitted to the box instead of in fixed zones, which a small subject cannot win
 * against a busy background (scripts/run_subject_classes.py). An ISP's phase-detection block
 * imposes rules on that window -- a minimum size, a coarse grid, inside the frame -- which
 * af_isp_fit applies. A new window also takes effect a frame or more after it is programmed;
 * that delay lives in the caller's pipeline (scripts/run_isp_window.py measures its cost).
 *
 * The ROI leads, depth vetoes: a measurement whose in-focus lens position disagrees with the
 * AF brain's prediction is dropped, at most max_veto - 1 frames in a row; then the detector
 * is trusted (the subject really moved).
 */
#ifndef AF_ROI_H
#define AF_ROI_H

#include "af_util.h"

typedef struct { int y0, y1, x0, x1; } af_win;        /* rows y0..y1-1, columns x0..x1-1 */
typedef struct { int align, min_w, min_h; } af_isp;   /* window rules of the ISP's PDAF block */

/* Generic placeholders, not any particular ISP's: an 8 px grid, windows >= 48 x 8 px. */
void   af_isp_default(af_isp *isp);
/* The window the ISP measures for a requested box: grown to the minimum size around the
 * box's centre, moved inside the width x height frame, snapped outward to the grid. */
af_win af_isp_fit(const af_isp *isp, af_win box, int width, int height);

typedef struct {
    af_real gate, c_floor, s0, p;  /* gate in sigmas; sigma(c) = s0 * max(c, c_floor)^-p mm */
    int max_veto, n;               /* the detector wins on the max_veto-th disagreement in a row */
} af_roi_gate;

void af_roi_gate_init(af_roi_gate *g);                /* 3.5 sigma, max_veto 6 */
/* 1 = use the measurement (in-focus lens position z mm, confidence conf) given the brain's
 * prediction (has, x, var); 0 = veto it this frame. Always 1 while there is no track. */
int  af_roi_gate_check(af_roi_gate *g, af_real z, af_real conf, int has, af_real x, af_real var);

#endif /* AF_ROI_H */
