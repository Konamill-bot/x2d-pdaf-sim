/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* af_phase.c -- see af_phase.h. Python reference: pdaf_sim/phase_corr.py. */
#include "af_phase.h"

unsigned long af_pc_sizeof(void) { return (unsigned long)sizeof(af_pc); }

int af_pc_init(af_pc *pc, int n, int max_disp)
{
    int m, nfft = 1, lg = 0;
    const af_real two_pi = (af_real)6.283185307179586476925286766559;   /* build precision: no soft-double on an MCU */
    if (n < 4 || n > AF_PC_MAX_N || max_disp < 1 || max_disp > AF_PC_MAX_DISP) return -1;
    while (nfft < 2 * n) { nfft <<= 1; lg++; }          /* numpy: 1 << ceil(log2(2n)) */
    if (nfft > AF_PC_MAX_NFFT || 2 * max_disp + 1 > nfft) return -1;
    pc->n = n; pc->nfft = nfft; pc->log2nfft = lg; pc->max_disp = max_disp;
    for (m = 0; m < n; m++)                              /* numpy.hanning(n) */
        pc->win[m] = (af_real)0.5 - (af_real)0.5 * AF_COS(two_pi * (af_real)m / (af_real)(n - 1));
    for (m = 0; m < nfft / 2; m++) {
        pc->twr[m] = AF_COS(two_pi * (af_real)m / (af_real)nfft);
        pc->twi[m] = -AF_SIN(two_pi * (af_real)m / (af_real)nfft);
    }
    return 0;
}

/* In-place iterative radix-2 complex FFT; inverse = conjugate twiddles (unscaled). */
static void fft(af_pc *pc, int inverse)
{
    int n = pc->nfft, i, j, len;
    af_real *re = pc->re, *im = pc->im;
    for (i = 1, j = 0; i < n; i++) {                     /* bit-reversal permutation */
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            af_real t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }
    for (len = 2; len <= n; len <<= 1) {
        int half = len >> 1, step = n / len, k;
        for (i = 0; i < n; i += len) {
            for (k = 0; k < half; k++) {
                af_real wr = pc->twr[k * step];
                af_real wi = inverse ? -pc->twi[k * step] : pc->twi[k * step];
                int a = i + k, b = i + k + half;
                af_real xr = re[b] * wr - im[b] * wi;
                af_real xi = re[b] * wi + im[b] * wr;
                re[b] = re[a] - xr; im[b] = im[a] - xi;
                re[a] += xr;        im[a] += xi;
            }
        }
    }
}

/* Core of estimate_disparity on the collapsed rows already in pc->l, pc->r. */
static void correlate(af_pc *pc, af_real *disp_px, af_real *conf)
{
    int n = pc->n, nfft = pc->nfft, md = pc->max_disp, k, j, peak;
    af_real lm = 0, rm = 0, pv, side, psr, c;
    for (k = 0; k < n; k++) { lm += pc->l[k]; rm += pc->r[k]; }
    lm /= (af_real)n; rm /= (af_real)n;
    /* pack both windowed, mean-removed, zero-padded signals into one complex FFT */
    for (k = 0; k < nfft; k++) {
        pc->re[k] = k < n ? (pc->l[k] - lm) * pc->win[k] : 0;
        pc->im[k] = k < n ? (pc->r[k] - rm) * pc->win[k] : 0;
    }
    fft(pc, 0);
    /* unpack L = (Z[k] + conj Z[N-k]) / 2, R = (Z[k] - conj Z[N-k]) / 2i, then
     * X = L conj(R) / sqrt(|L conj(R)| + 1e-8) for k = 0..N/2 (X is Hermitian) */
    for (k = 0; k <= nfft / 2; k++) {
        int m = (nfft - k) & (nfft - 1);
        af_real zr = pc->re[k], zi = pc->im[k], wr = pc->re[m], wi = -pc->im[m];   /* w = conj Z[N-k] */
        af_real lr = (af_real)0.5 * (zr + wr), li = (af_real)0.5 * (zi + wi);
        af_real rr = (af_real)0.5 * (zi - wi), ri = (af_real)-0.5 * (zr - wr);
        af_real xr = lr * rr + li * ri, xi = li * rr - lr * ri;                     /* L * conj(R) */
        af_real s = (af_real)1 / AF_SQRT(AF_SQRT(xr * xr + xi * xi) + (af_real)1e-8);
        pc->re[k] = xr * s; pc->im[k] = xi * s;
        if (m != k && k != 0) { pc->re[m] = xr * s; pc->im[m] = -xi * s; }
    }
    pc->im[0] = 0; pc->im[nfft / 2] = 0;              /* irfft ignores these imaginary parts */
    fft(pc, 1);
    for (j = 0; j <= 2 * md; j++) {                   /* lags -md .. +md */
        int lag = j - md;
        pc->corr[j] = pc->re[lag < 0 ? nfft + lag : lag] / (af_real)nfft;
    }
    peak = 0;
    for (j = 1; j <= 2 * md; j++) if (pc->corr[j] > pc->corr[peak]) peak = j;   /* first max */
    pv = pc->corr[peak];
    *disp_px = (af_real)(peak - md);
    if (peak > 0 && peak < 2 * md) {                  /* parabolic sub-pixel refinement */
        af_real a = pc->corr[peak - 1], b = pc->corr[peak + 1];
        af_real den = a - 2 * pv + b;
        if (AF_FABS(den) > (af_real)1e-9) *disp_px += (af_real)0.5 * (a - b) / den;
    }
    side = 0; k = 0;                                  /* sidelobe: max outside peak +-3 */
    for (j = 0; j <= 2 * md; j++) {
        if (j >= peak - 3 && j < peak + 4) continue;
        if (!k || pc->corr[j] > side) { side = pc->corr[j]; k = 1; }
    }
    if (!k) side = (af_real)1e-6;
    psr = pv / (side > (af_real)1e-6 ? side : (af_real)1e-6);
    c = AF_TANH((af_real)0.5 * (psr - 1));
    *conf = c > 0 ? c : 0;
}

void af_pc_strip(af_pc *pc, const float *L, const float *R, int rows, int stride,
                 af_real *disp_px, af_real *conf)
{
    int n = pc->n, i, k;
    for (k = 0; k < n; k++) { pc->l[k] = 0; pc->r[k] = 0; }
    for (i = 0; i < rows; i++)
        for (k = 0; k < n; k++) {
            pc->l[k] += (af_real)L[i * stride + k];
            pc->r[k] += (af_real)R[i * stride + k];
        }
    for (k = 0; k < n; k++) { pc->l[k] /= (af_real)rows; pc->r[k] /= (af_real)rows; }
    correlate(pc, disp_px, conf);
}

void af_pc_zones(af_pc *pc, const float *L, const float *R, int h, int n_zones,
                 af_real *disp_px, af_real *conf)
{
    int z, sh = h / n_zones > 0 ? h / n_zones : 1;
    for (z = 0; z < n_zones; z++) {
        int lo = z * sh, hi = (z < n_zones - 1) ? lo + sh : h;
        af_pc_strip(pc, L + lo * pc->n, R + lo * pc->n, hi - lo, pc->n, &disp_px[z], &conf[z]);
    }
}

void af_pc_combine(const af_real *disp_px, const af_real *conf, const unsigned char *mask,
                   int n, af_real *disp_out, af_real *conf_out)
{
    af_real d[AF_MAX_ZONES], buf[AF_MAX_ZONES], med, mean = 0, var = 0, cm = 0, agree, bonus = 0, c;
    int i, m = 0;
    for (i = 0; i < n && m < AF_MAX_ZONES; i++)
        if (!mask || mask[i]) { d[m] = disp_px[i]; cm += conf[i]; m++; }
    if (m == 0) { *disp_out = 0; *conf_out = 0; return; }
    med = af_median(d, m, buf);
    for (i = 0; i < m; i++) mean += d[i];
    mean /= (af_real)m;
    for (i = 0; i < m; i++) var += (d[i] - mean) * (d[i] - mean);
    agree = m > 1 ? AF_EXP(-AF_SQRT(var / (af_real)m) / 2) : (af_real)1;     /* np.std, ddof 0 */
    cm /= (af_real)m;
    if (AF_FABS(med) < 1 && agree > (af_real)0.7) bonus = (af_real)0.25 * (1 - AF_FABS(med));
    c = cm * agree + bonus;
    *disp_out = med;
    *conf_out = c < 1 ? c : 1;
}
