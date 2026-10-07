/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
#include "af_roi.h"

void af_isp_default(af_isp *isp) { isp->align = 8; isp->min_w = 48; isp->min_h = 8; }

static int floor_half(int v) { return v >= 0 ? v / 2 : -((1 - v) / 2); }   /* Python's v // 2 */

af_win af_isp_fit(const af_isp *isp, af_win b, int width, int height)
{
    int a = isp->align > 0 ? isp->align : 1;
    if (b.x1 - b.x0 < isp->min_w) { b.x0 = floor_half(b.x0 + b.x1 - isp->min_w); b.x1 = b.x0 + isp->min_w; }
    if (b.y1 - b.y0 < isp->min_h) { b.y0 = floor_half(b.y0 + b.y1 - isp->min_h); b.y1 = b.y0 + isp->min_h; }
    if (b.x0 < 0) { b.x1 -= b.x0; b.x0 = 0; }
    if (b.x1 > width) { b.x0 -= b.x1 - width; b.x1 = width; }
    if (b.y0 < 0) { b.y1 -= b.y0; b.y0 = 0; }
    if (b.y1 > height) { b.y0 -= b.y1 - height; b.y1 = height; }
    b.x0 = (b.x0 > 0 ? b.x0 : 0) / a * a;
    b.y0 = (b.y0 > 0 ? b.y0 : 0) / a * a;
    b.x1 = (b.x1 + a - 1) / a * a; if (b.x1 > width) b.x1 = width;
    b.y1 = (b.y1 + a - 1) / a * a; if (b.y1 > height) b.y1 = height;
    return b;
}

void af_roi_gate_init(af_roi_gate *g)
{
    g->gate = (af_real)3.5; g->c_floor = (af_real)0.05; g->s0 = (af_real)0.031; g->p = (af_real)1.58;
    g->max_veto = 6; g->n = 0;
}

int af_roi_gate_check(af_roi_gate *g, af_real z, af_real conf, int has, af_real x, af_real var)
{
    af_real c = conf > g->c_floor ? conf : g->c_floor, sig, dz;
    if (!has) return 1;
    sig = g->s0 * AF_POW(c, -g->p);
    dz = z - x;
    if (dz * dz > g->gate * g->gate * (var + sig * sig) && ++g->n < g->max_veto) return 0;
    g->n = 0;
    return 1;
}
