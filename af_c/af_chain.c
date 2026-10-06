/* ---------------------------------------------------------------------------
 * DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
 * firmware, no .cim files, and no decrypted/extracted firmware data (none is
 * required to run it). This is an idealized model for studying autofocus
 * ALGORITHMS; it does NOT represent any product's actual implementation.
 * Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
 * without warranty of any kind. Use at your own risk.
 * ------------------------------------------------------------------------- */
/* af_chain.c -- see af_chain.h. Python reference: scripts/run_aft_tracker.py::run_chain. */
#include "af_chain.h"

unsigned long af_chain_sizeof(void) { return (unsigned long)sizeof(af_chain); }

int af_chain_init(af_chain *ch, int mode, int width, int height, int n_zones,
                  int max_disp_px, af_real px_per_mm)
{
    af_params bp; af_track_params tp;
    if (n_zones < 1 || n_zones > AF_MAX_ZONES || height < n_zones || px_per_mm <= 0) return -1;
    if (af_pc_init(&ch->pc, width, max_disp_px) != 0) return -1;
    ch->mode = mode; ch->height = height; ch->n_zones = n_zones; ch->px_per_mm = px_per_mm;
    af_default_params(&bp); af_init(&ch->brain, &bp);
    af_track_default_params(&tp); af_track_init(&ch->tr, &tp);
    ch->held = 0;
    return 0;
}

af_real af_chain_frame(af_chain *ch, const float *L, const float *R, af_real lens)
{
    af_real d, c, x = 0, var = 0;
    int i, nz = ch->n_zones, any = 0;
    af_pc_zones(&ch->pc, L, R, ch->height, nz, ch->disp, ch->conf);
    ch->held = 0;
    if (ch->mode == AF_MODE_C) {
        af_pc_combine(ch->disp, ch->conf, 0, nz, &d, &c);
        return af_step(&ch->brain, d / ch->px_per_mm, c, lens);
    }
    for (i = 0; i < nz; i++) ch->z[i] = lens - ch->disp[i] / ch->px_per_mm;
    {
        int has = af_predict(&ch->brain, &x, &var);
        if (!af_track_select(&ch->tr, ch->z, ch->conf, nz, has, x, var, ch->mask)) {
            ch->held = 1;
            return af_coast(&ch->brain);                 /* subject hidden */
        }
    }
    for (i = 0; i < nz; i++) any |= ch->mask[i];
    af_pc_combine(ch->disp, ch->conf, any ? ch->mask : 0, nz, &d, &c);
    return af_step(&ch->brain, d / ch->px_per_mm, c, lens);
}

af_real af_chain_coast(af_chain *ch)
{
    ch->held = 0;
    return af_coast(&ch->brain);
}
