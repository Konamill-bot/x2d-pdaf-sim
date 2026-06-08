# Letter 2 — Draft

Two versions: English (to send) and Chinese (for your own reference).
Send English. Keep tone consistent with Letter 1: technical, calm,
respectful, no demands. State observations, present analysis, ask one
clear question.

---

## English version (send this)

Subject: Follow-up to AF-C inquiry — X2D 100C autofocus behavioural study

Dear Hasselblad Product Team,

Thank you for the prompt acknowledgement of my earlier letter regarding
AF-C on the X2D 100C. While that review is in progress, I want to share
follow-up technical work I have completed independently, in the spirit
of being a useful interlocutor rather than only an asker.

In the days following my first letter, I conducted systematic direct
observation of my X2D 100C (firmware 4.2.0, XCD 2,5/55V), built an
open-source simulation testbench for PDAF autofocus decision policies,
and cross-referenced findings against published reviews and patent
literature. The work is at:

  https://github.com/Konamill-bot/x2d-pdaf-sim

I want to be transparent about what it is and is not. It is a *public*,
*reproducible*, *self-contained* study using synthetic scenes and a thin-
lens optical model. It is not a claim about Hasselblad's internal
implementation, and it is not a request that you adopt my code. It is
intended as a shared frame of reference so any technical discussion we
might have starts from the same definitions.

The behavioural observations and the simulated improvements together
point at three firmware-level changes that appear to substantially
close the AF performance gap between the X2D 100C and current peer
mirrorless bodies, without requiring any hardware change:

1. **AF decision-loop framerate (conditional claim).** Modern
   on-sensor PDAF uses a readout channel architecturally distinct from
   the imaging pixel readout — PDAF pixels are typically excluded from
   the imaging-pixel binning that drives live-view, and exposed through
   a separate readout path whose rate is a configuration choice. The
   X2D's 5.76 M-dot EVF and 2.36 M-dot rear LCD establish that the
   sensor sustains high-framerate subsampled output during live view.

   I want to be careful here: live-view EVF throughput does not, on
   its own, prove that the AF pipeline (sensor → PDAF readout → ISP
   scheduling → decision logic → lens motor command) can also be
   driven at the same rate. ISP scheduling load and internal bus
   bandwidth between the sensor and SoC are factors only Hasselblad
   can measure internally. What the EVF demonstrates is that the
   *sensor* side is not the bottleneck; the remaining question is
   whether ISP scheduling permits a faster AF loop, and that is a
   firmware-level investigation rather than a hardware change.

   The simulation result is therefore conditional in form: *if* AF
   decision-loop framerate can be raised from approximately 15 fps
   to 60 fps, *then* hunting sweeps drop from 30 to 0 on a
   low-contrast static target and lock time falls to 0.18 seconds.
   Whether the antecedent holds in the X2D pipeline is exactly the
   question I am unable to answer from outside the camera.

2. **Temporal prior on PDAF measurements.** Replacing single-frame
   PSR thresholding with a confidence-weighted Kalman filter over
   lens position reduces simulated hunting sweeps by 63 percent on a
   low-contrast static target at 15 fps. The compute cost is on the
   order of microseconds per frame on any modern application-class
   processor, independent of sensor readout.

3. **Multi-zone confidence agreement.** Querying several PDAF zones
   in parallel and weighting confidence by inter-zone agreement
   correctly suppresses false peaks on aliased signals (periodic
   patterns) and improves robustness on subjects with small motion.

The combination of all three produces, in simulation, a lock time of
approximately 0.22 seconds on a low-contrast target where the
simulated 15 fps baseline does not lock within the two-second
observation window. The repository's `out/stacked_comparison.png` is
the headline figure.

A fourth experiment (v3) stacks deadband control, PID lens drive, and
PDAF/CDAF fusion on top of the three above. The result is honestly
mixed: v3 reduces lens motor travel by roughly 30 percent and
eliminates the residual sweep entirely, in exchange for about 100 ms
slower lock on low-contrast subjects. I include it in the repository
because the trade-off itself is informative — it suggests the
firmware design choice between "fastest possible lock" and "smoothest
mechanical behaviour with longest motor life" is real, and that there
may be value in exposing this as a user preference.

These observations are anchored by direct comparison with my own Sony
A7 IV (which on an all-white wall produces a single ~0.7 second hunt
followed by a clear failure indicator), and by published evidence that
Fujifilm achieved substantial AF improvements from GFX 100S to
GFX 100S II without changing PDAF hardware — purely through what
Capture Integration called an "improved predictive AF algorithm."

I am aware the X2D II 100C addresses AF via LiDAR. LiDAR provides
range and is a meaningful hardware improvement; it does not, however,
solve the failure modes I infer from my observations — in particular
a near-focus AF failure consistent with PDAF correlation-peak
broadening at zero defocus, and stochastic same-scene behaviour
consistent with a stateless single-frame decision policy. The
firmware-level levers above appear to have independent value on the
X2D 100C.

My question remains the same as in Letter 1, only more specific:

**Is there a path under which firmware-level AF improvements to the
X2D 100C could be considered — whether as a paid capability upgrade,
a routine firmware revision, or a hardware-limitation reply that
honestly closes the topic?**

I do not expect a quick response; please take the time you need. I
have no expectation that my work be adopted. I plan to continue
documenting findings on the public repository as work progresses, in
the same calm and non-confrontational tone as this letter. I would
simply value knowing whether the door is open.

Thank you again for your time.

Sincerely,
Chan Kam Chi
Hasselblad 500C/A12 · X2D 100C (fw 4.2.0) · XCD 2,5/55V · Hasselblad Masters 2026
Public technical notes:
  github.com/Konamill-bot/x2d-cim-notes
  github.com/Konamill-bot/x2d-pdaf-sim   (this study)

---

## Chinese reference version (for your own clarity, do not send)

主题:AF-C 查询的技术后续 — X2D 100C 自动对焦行为研究

亲爱的 Hasselblad 产品团队,

感谢你们对我上一封 AF-C 信件的迅速回应。在你们评估期间,我想分享
我独立完成的后续技术工作 —— 作为一个有用的对话者,而不只是请求者。

第一封信之后的几天里,我系统地直接观察了自己的 X2D 100C(固件 4.2.0,
XCD 2,5/55V),搭建了一个开源的 PDAF 自动对焦决策策略仿真 testbench,
并把发现跟公开 review、专利文献交叉验证。项目在:

  https://github.com/Konamill-bot/x2d-pdaf-sim

我想坦白说明它**是什么**和**不是什么**。它是一个**公开**、**可复现**、
**自包含**的研究,使用合成场景和薄透镜光学模型。它不是对 Hasselblad
内部实现的任何 claim,也不是要求你们采纳我的代码。它的存在是为了
任何可能的技术对话都从同一套定义出发。

行为观察 + 仿真改进共同指向三个 firmware-level 改动,似乎能在不需要
任何硬件变更的前提下,显著缩小 X2D 100C 与当前同类无反相机之间的
AF 性能差距:

1. **半按时的 AF readout binning**。X2D 的 BSI 传感器几乎肯定原生
   支持 binned readout。半按 AF 时切到 4x4 binning(拍摄时切回 full
   readout)将有效 AF 帧率从约 15 fps 提升到 60 fps。仿真中,仅此
   一项就把低对比目标上的 hunting sweep 从 30 次降到 0 次,锁焦时
   间 0.18 秒。

2. **PDAF 测量的时间先验**。用 confidence-weighted Kalman filter
   替换单帧 PSR 阈值,在低对比场景下仿真 hunting sweep 减少 63%。
   计算开销在任何 Cortex-A class CPU 上都是微秒量级,跟传感器读出
   pipeline 无关。

3. **Multi-zone confidence 一致性**。并行查询多个 PDAF zones,用
   zone 间一致性加权 confidence,能正确压制周期信号的假峰值,并
   改善小幅运动主体的鲁棒性。

三者组合,仿真上在低对比目标(当前行为 baseline 永远无法锁住)上
锁焦时间约 0.22 秒。仓库里的 `out/stacked_comparison.png` 是头图。

这些观察以我自己的 Sony A7 IV 直接对比锚定(全白墙场景上 ~0.7 秒
单次 hunt 后明确失败指示),以及 Fujifilm 从 GFX 100S 到 100S II
在不更换 PDAF 硬件的前提下大幅改进 AF 的公开证据(Capture
Integration 称之为 "improved predictive AF algorithm")。

我知道 X2D II 100C 用 LiDAR 解决 AF 问题。LiDAR 提供测距,是有意义
的硬件改进;但它并不解决我识别的失败模式(特别是近焦点 PSR
confidence 下降和无状态单帧决策策略)。上述 firmware-level lever
似乎有独立价值,X2D 100C 能从中获益,无需购买 X2D II。

我的问题跟 Letter 1 一样,只是更具体:

**有没有一条路径,X2D 100C 的 firmware-level AF 改进能被考虑 ——
不论是付费 capability upgrade、routine firmware revision、还是
诚实关闭话题的"硬件限制"回复?**

我不期望我的工作被采纳。无论你们怎么回复,我都会继续以与本信
相同的冷静、不对抗的方式向公开仓库发布更多发现。我只是想知道
门是否开着。

再次感谢你们的时间。

Chan Kam Chi 敬上

---

## When to send

Wait until Hasselblad responds to Letter 1, or until 10 business days
have passed from their acknowledgement -- whichever comes first.
Sending too early signals impatience.

When you do send, the subject line should reference Letter 1 explicitly
(e.g. "Follow-up to AF-C inquiry") so it routes to the same person.
