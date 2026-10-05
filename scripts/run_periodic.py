# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Hard high-contrast case even flagship AF fails: PERIODIC patterns (fabric,
fence, brick, keyboard). This isolates the PHASE-WRAP physics directly:
a periodic subject gives a strong (high-confidence) correlation, but the
disparity is only known MODULO the pattern period -- so when the lens is far
from focus the measured disparity WRAPS and reads ~0 at several false planes.
PDAF then locks CONFIDENTLY on a WRONG plane (false positive). Phase detection
fundamentally cannot resolve this (Sony/Canon/Nikon included).

CDAF (contrast) has NO periodic ambiguity -- it peaks only at true focus -- so a
CDAF-coarse + PDAF-fine hybrid disambiguates. Pure PDAF cannot.

Run: python scripts/run_periodic.py -> out/periodic.png
"""
import os, sys
import numpy as np, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FPS=60; N=170; LO,HI=0.0,7.0; MAXSTEP=10000/300/FPS; MOTOR=0.6
TGT=1.555                      # true focus (2 m)
DISP_PER_MM=4.0                # px of disparity per mm defocus
PERIOD_PX=6.0                  # pattern period -> aliasing every PERIOD/DISP mm
ALIAS_MM=PERIOD_PX/DISP_PER_MM # false-plane spacing
START=6.5
rng=np.random.default_rng(1)

def pdaf(lens):
    """Periodic subject: strong correlation (high conf) but disparity wraps mod period."""
    true_disp = DISP_PER_MM*(lens-TGT)
    wrapped = ((true_disp+PERIOD_PX/2) % PERIOD_PX) - PERIOD_PX/2   # phase ambiguity
    wrapped += rng.normal(0,0.1)
    return wrapped/DISP_PER_MM, 0.85            # high confidence, periodic = strong
def cdaf(lens):
    return float(np.exp(-((lens-TGT)/0.9)**2) + rng.normal(0,0.01))  # unambiguous peak

def run_A():                                    # pure PDAF -> drives to wrapped-zero -> false lock
    lens=START; traj=[]
    for k in range(N):
        d,c=pdaf(lens); cmd=lens-d
        st=float(np.clip(cmd-lens,-MAXSTEP,MAXSTEP)); lens=float(np.clip(lens+MOTOR*st,LO,HI)); traj.append(lens)
    return np.array(traj)

def run_B():                                    # CDAF coarse (find true peak) then PDAF fine
    lens=START; traj=[]; best=(-1.0,lens); prev=None; d_dir=-1; mode="coarse"
    for k in range(N):
        if mode=="coarse":
            cs=cdaf(lens)
            if cs>best[0]: best=(cs,lens)
            if prev is not None and cs<best[0]-0.15 and k>6: mode="fine"; cmd=best[1]
            else:
                cmd=lens+d_dir*0.25
                if lens<=LO: d_dir=1
            prev=cs
        else:
            d,c=pdaf(lens); cmd=lens-d if abs(d)<PERIOD_PX/2/DISP_PER_MM else best[1]
        st=float(np.clip(cmd-lens,-MAXSTEP,MAXSTEP)); lens=float(np.clip(lens+MOTOR*st,LO,HI)); traj.append(lens)
    return np.array(traj)

os.makedirs("out",exist_ok=True); A=run_A(); B=run_B(); k=np.arange(N)
fig,ax=plt.subplots(1,1,figsize=(11,5.5))
ax.axhline(TGT,color="k",ls="--",lw=1.5,label=f"true focus ({TGT:.2f}mm = 2m)")
first=True
for m in range(-4,5):
    if m==0: continue
    yy=TGT+m*ALIAS_MM
    if LO<yy<HI:
        ax.axhline(yy,color="#e36",ls=":",lw=0.9,label="false (aliased) plane" if first else None); first=False
ax.plot(k,A,color="#D85A30",lw=2,label="A: PDAF only (locks FALSE plane, high conf)")
ax.plot(k,B,color="#1D9E75",lw=2,label="B: CDAF-coarse + PDAF-fine (true)")
ax.set_xlabel("frame (60fps)"); ax.set_ylabel("lens pos (mm)"); ax.set_ylim(LO,HI)
ax.set_title(f"Periodic pattern aliasing (false planes every {ALIAS_MM:.2f}mm): PDAF confidently wrong, CDAF saves it")
ax.legend(fontsize=8,loc="center right")
fig.tight_layout(); fig.savefig("out/periodic.png",dpi=120)
print(f"true={TGT}mm  alias spacing={ALIAS_MM:.2f}mm  start={START}mm")
print(f"A PDAF-only  final={A[-1]:.2f}mm err={abs(A[-1]-TGT):.2f}mm  {'FALSE LOCK' if abs(A[-1]-TGT)>0.3 else 'ok'}")
print(f"B CDAF+PDAF  final={B[-1]:.2f}mm err={abs(B[-1]-TGT):.2f}mm  {'wrong' if abs(B[-1]-TGT)>0.3 else 'OK (true)'}")
print("saved out/periodic.png")
