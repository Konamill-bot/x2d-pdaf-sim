# ---------------------------------------------------------------------------
# DISCLAIMER: Personal research only. SIMULATION CODE ONLY -- contains no
# firmware, no .cim files, and no decrypted/extracted firmware data (none is
# required to run it). This is an idealized model for studying autofocus
# ALGORITHMS; it does NOT represent any product's actual implementation.
# Not affiliated with or endorsed by Hasselblad or DJI. Provided "AS IS",
# without warranty of any kind. Use at your own risk.
# ---------------------------------------------------------------------------
"""Where the BIG AF-C improvement actually is: not the temporal filter, but the
SYSTEM config -- AF loop RATE and lens SPEED CAP. Both are firmware/register
knobs (the algorithms already exist), so these are MINOR adjustments to the
built-in that give LARGE gains. Filter choice (firmware vs Kalman) is a minor
lever by comparison. Fixed policy = firmware; sweep rate x speed.
Run: python scripts/run_big_levers.py -> out/big_levers.png
"""
import os, sys
from dataclasses import dataclass, field
import numpy as np, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdaf_sim.scene import high_contrast
from pdaf_sim.dualpixel import render_lr
from pdaf_sim.phase_corr import estimate_disparity_multi_zone
from pdaf_sim.psf import signed_disparity_px
from pdaf_sim.policy import Decision, TemporalPolicy
from pdaf_sim.latency import LatencyBuffer

F,FN,DI,PX=55.,2.5,1500.,3.76; PPM=signed_disparity_px(1.,F,FN,DI,PX)
FPS=60; N=1800; LO,HI=0.,7.; MOTOR=0.6; LAT=3; DB=0.10
def fmm(m): return float(np.clip(F*F/(max(m*1000,F+1)-F),LO,HI))
def subj(k):
    s=k/FPS
    if s<18: return 4.0-3.0*(s/18)       # walk-in 4m->1m
    if s<22: return 1.0
    if s<23: return 3.0                  # step far
    if s<30: return 3.0
    return 0.8
@dataclass
class Firmware:
    tau:float=0.08; step:float=0.3; _e:float|None=None; _d:int=1
    def s(self,d,c,lens):
        if c<self.tau:
            cmd=lens+self._d*self.step
            if cmd>HI or cmd<LO:self._d*=-1;cmd=lens+self._d*self.step
            self._e=cmd;return cmd
        z=lens-d; self._e=z if self._e is None else .65*self._e+.35*z; return float(self._e)
    def coast(self): return float(self._e or 0.)

def sim(policy_kind, update_every, cap, seed=1):
    rng=np.random.default_rng(seed); nrng=np.random.default_rng(seed+7)
    sharp=high_contrast(rng); lb=LatencyBuffer(LAT); maxstep=cap/300/FPS
    lens=fmm(subj(0)); last=lens; infoc=0
    if policy_kind=="kalman": pol=TemporalPolicy(tau_sweep=.05,process_var=.5*15/FPS,meas_var_base=1.,predict_frames=LAT,sweep_step=.3,scan_lo=LO,scan_hi=HI)
    else: pol=Firmware()
    for k in range(N):
        tgt=fmm(subj(k))
        if k%update_every==0:
            L,R=render_lr(sharp,lens-tgt,F,FN,DI,PX,noise_sigma=0.01,rng=nrng)
            d,c=estimate_disparity_multi_zone(L,R,max_disp_px=48,n_zones=14)
            arr=lb.push_pop((d/PPM,c,lens))
            if arr is not None:
                last = pol.s(arr[0],arr[1],arr[2]) if policy_kind=="firmware" else pol.step(arr[0],arr[1],arr[2]).lens_cmd
            elif hasattr(pol,"coast"):
                cc=pol.coast(); last=cc.lens_cmd if hasattr(cc,"lens_cmd") else cc
        st=float(np.clip(last-lens,-maxstep,maxstep)); lens=float(np.clip(lens+MOTOR*st,LO,HI))
        if abs(lens-tgt)<DB: infoc+=1
    return 100*infoc/N

os.makedirs("out",exist_ok=True)
rates=[1,2,4,8]; rate_hz=[FPS//r for r in rates]; caps=[4000,10000,24000]
grid=np.zeros((len(caps),len(rates)))
for i,cap in enumerate(caps):
    for j,r in enumerate(rates):
        grid[i,j]=sim("firmware",r,cap)
# filter comparison at a fixed mid config (rate=2, cap=10000)
fw=sim("firmware",2,10000); kf=sim("kalman",2,10000)
fig,ax=plt.subplots(1,2,figsize=(14,5.5))
x=np.arange(len(rates)); w=0.25
for i,cap in enumerate(caps):
    ax[0].bar(x+(i-1)*w, grid[i], w, label=f"speed cap {cap}")
ax[0].set_xticks(x); ax[0].set_xticklabels([f"{h:.0f}Hz\n(every {r}f)" for h,r in zip(rate_hz,rates)])
ax[0].set_ylabel("in-focus %"); ax[0].set_title("BIG lever: AF loop RATE x lens SPEED CAP (fixed firmware policy)")
ax[0].legend(fontsize=8); ax[0].set_ylim(0,100)
ax[1].bar(["firmware\nfilter","Kalman\nfilter"],[fw,kf],color=["#D85A30","#1D9E75"],width=0.5)
ax[1].set_ylabel("in-focus %"); ax[1].set_title("minor lever: filter swap (same config: 30Hz, cap 10000)")
ax[1].set_ylim(0,100)
for b,v in zip(ax[1].patches,[fw,kf]): ax[1].text(b.get_x()+b.get_width()/2,v+1,f"{v:.0f}%",ha="center",fontsize=10)
fig.suptitle("Big improvement = loop rate + speed cap (config), NOT the temporal filter",fontsize=12)
fig.tight_layout(rect=[0,0,1,.96]); fig.savefig("out/big_levers.png",dpi=120)
print("in-focus% grid (rows=speed cap 4000/10000/24000, cols=rate every 1/2/4/8f):")
for i,cap in enumerate(caps): print(f"  cap{cap:>6}: "+"  ".join(f"{grid[i,j]:5.1f}" for j in range(len(rates))))
print(f"\nfilter swap @ (30Hz, cap10000):  firmware={fw:.1f}%   Kalman={kf:.1f}%  (delta {kf-fw:+.1f})")
print(f"config swap (slow->fast): {grid[0,-1]:.1f}% -> {grid[2,0]:.1f}%  (delta {grid[2,0]-grid[0,-1]:+.1f})")
print("saved out/big_levers.png")
