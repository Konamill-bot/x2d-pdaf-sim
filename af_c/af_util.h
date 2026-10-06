/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* af_util.h -- shared scalar type and small helpers (header-only, internal). */
#ifndef AF_UTIL_H
#define AF_UTIL_H

#include <math.h>

#ifdef AF_REAL_DOUBLE
typedef double af_real;
#define AF_POW  pow
#define AF_SQRT sqrt
#define AF_EXP  exp
#define AF_TANH tanh
#define AF_COS  cos
#define AF_SIN  sin
#define AF_FABS fabs
#else
typedef float af_real;
#define AF_POW  powf
#define AF_SQRT sqrtf
#define AF_EXP  expf
#define AF_TANH tanhf
#define AF_COS  cosf
#define AF_SIN  sinf
#define AF_FABS fabsf
#endif

#define AF_MAX_ZONES 32

/* median of n values (numpy semantics: mean of the two middle values when n is even);
 * buf is scratch of at least n elements */
static inline af_real af_median(const af_real *v, int n, af_real *buf)
{
    int i, j;
    if (n <= 0) return 0;
    for (i = 0; i < n; i++) {                 /* insertion sort: n <= AF_MAX_ZONES */
        af_real x = v[i];
        for (j = i - 1; j >= 0 && buf[j] > x; j--) buf[j + 1] = buf[j];
        buf[j + 1] = x;
    }
    return (n & 1) ? buf[n / 2] : (af_real)0.5 * (buf[n / 2 - 1] + buf[n / 2]);
}

#endif /* AF_UTIL_H */
