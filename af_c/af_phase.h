/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/*
 * af_phase.h -- the AF "eyes": PDAF phase correlation per AF zone.
 *
 * Mirrors pdaf_sim/phase_corr.py (estimate_disparity, and the combination step of
 * estimate_disparity_multi_zone, which pdaf_sim/zone_tracker.py exposes as combine_zones).
 *
 * For each zone (a band of rows of the left/right PDAF views): average the rows, remove
 * the mean, apply a Hann window, cross-correlate L and R in the frequency domain with
 * partial phase weighting (|X|^-1/2), find the peak lag with parabolic sub-pixel
 * refinement, and score it by peak-to-sidelobe ratio: confidence = tanh((PSR-1)/2).
 *
 * C99, no heap: the af_pc workspace (sized at compile time) holds the window, the FFT
 * twiddles and all scratch.
 */
#ifndef AF_PHASE_H
#define AF_PHASE_H

#include "af_util.h"

#define AF_PC_MAX_N    512                /* max view width (pixels)        */
#define AF_PC_MAX_NFFT 1024               /* next pow2 >= 2 * AF_PC_MAX_N   */
#define AF_PC_MAX_DISP 128                /* max disparity search (pixels)  */

typedef struct {
    int n, nfft, log2nfft, max_disp;
    af_real win[AF_PC_MAX_N];                          /* Hann window          */
    af_real twr[AF_PC_MAX_NFFT / 2], twi[AF_PC_MAX_NFFT / 2];  /* e^{-2 pi i m / nfft} */
    af_real re[AF_PC_MAX_NFFT], im[AF_PC_MAX_NFFT];    /* FFT scratch          */
    af_real l[AF_PC_MAX_N], r[AF_PC_MAX_N];            /* collapsed zone rows  */
    af_real corr[2 * AF_PC_MAX_DISP + 1];
} af_pc;

/* sizeof(af_pc), for hosts that allocate it opaquely (e.g. the Python ctypes bridge) */
unsigned long af_pc_sizeof(void);

/* n = view width in pixels, max_disp = disparity search range (pixels). 0 on success. */
int  af_pc_init(af_pc *pc, int n, int max_disp);

/* One zone: rows x n pixels, row-major with row stride `stride` (floats). */
void af_pc_strip(af_pc *pc, const float *L, const float *R, int rows, int stride,
                 af_real *disp_px, af_real *conf);

/* Every zone of h x n views split into n_zones bands (the last band takes the remainder). */
void af_pc_zones(af_pc *pc, const float *L, const float *R, int h, int n_zones,
                 af_real *disp_px, af_real *conf);

/* Combine the zones where mask[i] != 0 (mask NULL = all): median disparity and
 * agreement-weighted confidence with the near-focus bonus. */
void af_pc_combine(const af_real *disp_px, const af_real *conf, const unsigned char *mask,
                   int n, af_real *disp_out, af_real *conf_out);

#endif /* AF_PHASE_H */
