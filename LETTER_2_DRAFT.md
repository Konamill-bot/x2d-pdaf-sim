# Letter 2 — Draft

Two versions: English (to send) and Chinese (for your own reference).
Send English. Keep tone consistent with Letter 1: technical, calm,
respectful, no demands. State observations, present analysis, ask one
clear question.

**PRE-SEND CHECKLIST (do these in order before sending):**
1. The letter references `https://github.com/Konamill-bot/x2d-pdaf-sim`
   in four places throughout (English body + signature, Chinese body +
   signature). This repository does not yet exist publicly. Push the
   local repo to GitHub following `GITHUB_PUSH.md` BEFORE sending this
   letter. If the repo is not yet public, the link will 404 and damage
   credibility.
2. Verify Hasselblad has responded to Letter 1 (or 10 business days
   have passed from their acknowledgement) -- see "When to send"
   section at the end of this file.
3. Skim the letter once for the five numbers that hit hardest if
   wrong: 1000 seeds, 47 ± 35 % (D low-contrast IMX461 sim),
   98.5 ± 0.4 % (D high-contrast), 64.6 ± 43 % (baseline high-contrast),
   5 ± 11 % (C alone low-contrast). These must match
   `out/imx461_full_stack_metrics.png`.
4. Optional but recommended: have a second person (or another model)
   re-read the English version for tone before sending.

---

## English version (send this)

Subject: Follow-up to AF-C inquiry — X2D 100C autofocus behavioural study

Dear Hasselblad Product Team,

Thank you for the prompt acknowledgement of my earlier letter regarding
AF-C on the X2D 100C. While that review is in progress, I want to share
follow-up technical work I have completed independently, in the spirit
of being a useful interlocutor rather than only an asker.

Before the technical content, one thing I want to be clear about. The
X2D 100C continues to produce images I could not make with any other
camera. The HNCS rendering at base ISO, the dynamic range I get in
mixed light, the mechanical feel of the XCD 55V focus ring, the
quietness of the leaf shutter — none of these are in question for me.
This letter is not a complaint about a camera I regret buying. It is
precisely because the X2D earns its place in everything else that the
AF behaviour stands out — it is the one part of the experience that
does not match the rest. That is why I am willing to spend my own
time on it, and why I think it is worth your time too.

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

I want to lead with the most important finding from the full
lever-by-lever experiment (`scripts/run_imx461_stats.py`,
`out/imx461_full_stack_metrics.png`), based on 1000 independent
seeds per configuration on the 294-zone IMX461 simulator:
**the combination that produces a reliable win across both
low-contrast and high-contrast scenes is sensor binning + Kalman
temporal prior + multi-zone confidence aggregation.** Earlier
runs at fewer seeds showed misleading results (different seeds
favoured different configurations); reporting now with 1000-seed
means and standard deviations is what changed the recommendation.

All numbers below come from a simulation modelled on the Sony IMX461
sensor used in the X2D 100C: 21 × 14 = 294 PDAF zones tiled across
the active area, per-zone phase correlation at the native 3.76 μm
pitch (PDAF rows cannot be binned without destroying sub-aperture
phase signal), multi-zone configurations querying the nearest five
zones to subject and aggregating by confidence-weighted agreement.
Bar chart in `out/imx461_full_stack_metrics.png`. 1000 seeds per
configuration, mean standard error ~0.5 %.

Low-contrast static target (in-focus % = fraction of frames within
0.3 mm of target):

| Configuration                                  | in-focus %   |
|------------------------------------------------|--------------|
| A. baseline (stateless, 15 fps, single zone)   | 0.0 ± 0      |
| B. + 4x4 binning (60 fps) only                 | 0.0 ± 0      |
| C. + Kalman temporal prior (single zone)       | 5.2 ± 11.3   |
| **D. + multi-zone aggregation (nearest 5)**    | **47.1 ± 35.4** |
| E. + deadband + PID + CDAF fusion (V3 stack)   | 36.6 ± 42.2  |

High-contrast static target (same 1000 seeds):

| Configuration                                  | in-focus %     |
|------------------------------------------------|----------------|
| A. baseline                                    | **64.6 ± 42.5** |
| C. + Kalman                                    | 98.2 ± 0.9     |
| **D. + multi-zone**                            | **98.5 ± 0.4** |
| E. + V3                                        | 0.8 ± 8.8 (currently broken; see below) |

I want to call out one specific number in the high-contrast table:
**the baseline scores 65 ± 43 %**, meaning the current single-frame
PSR-threshold policy succeeds on roughly two thirds of the seeds and
fails catastrophically on the rest, even on high-contrast scenes
where it should succeed comfortably. This statistical signature
matches a direct observation I made on my own X2D: the same scene,
half-pressed multiple times, sometimes locks immediately and
sometimes hunts. The simulation reproducing this stochasticity from
first principles (stateless single-frame decisions on noisy PDAF
correlations) is the only point in this study where my synthetic
model and direct camera behaviour cross-validated independently.
That gives me modest confidence the rest of the model's predictions
are at least in the right qualitative ballpark.

The four non-obvious findings from this matrix:

1. **AF-readout framerate alone (config B) does not help — it makes
   hunting worse.** Without a temporal prior to integrate measurements,
   raising the framerate from 15 to 60 fps simply quadruples the rate
   at which low-confidence single-frame decisions are made. Lens
   motor travel rises from 5.8 mm to 23.8 mm in 2 seconds. Binning
   and temporal prior must be paired.

2. **Configuration D is the recommended target.** Binning + Kalman +
   nearest-5 multi-zone aggregation produces 47 ± 35 % in-focus on
   low-contrast and 98.5 ± 0.4 % on high-contrast (over 1000
   independent seeds on the 294-zone IMX461 simulator, mean SEM ~0.5 %).
   Compute cost is on the order of microseconds per frame on any
   modern application-class processor. To put it more precisely:
   **configuration D is not a proposal for better AF-S. It is, in
   effect, a working AF-C loop — a Kalman-filtered, multi-zone,
   high-framerate decision system that tracks focus continuously
   across frames.** The only difference between this simulation and
   an in-camera AF-C implementation is the firmware layer that
   enables it.

3. **Multi-zone aggregation is essential on low-contrast, not optional.**
   Configuration C (Kalman without multi-zone) achieves only 5 ± 11 %
   in-focus on low-contrast — effectively failure. The 9× lift from
   adding nearest-5 aggregation is the single largest improvement in
   the matrix. On high-contrast scenes C and D are statistically
   equivalent (98.2 ± 0.9 vs 98.5 ± 0.4), so multi-zone is free on
   easy scenes and necessary on hard ones — a dominant strategy.

4. **The full V3 stack (config E) is currently not recommended.**
   On the 294-zone IMX461 simulator E scores 37 ± 42 % on low-contrast
   and 1 ± 9 % on high-contrast. The deadband and PID parameters that
   worked on the earlier single-strip simulation interact badly with
   multi-zone aggregated confidence inputs; investigation continues
   in the repository. I include it here for transparency rather than
   as a recommendation.

The two conditional caveats from the first lever still apply: ISP
scheduling load and internal bus bandwidth between sensor and SoC are
factors only Hasselblad can measure, so configuration D is stated as
*if* the AF loop can be driven at 60 fps via PDAF channel
configuration, *then* the result above holds. The X2D's sensor is a
Sony IMX461 whose datasheet explicitly documents support for vertical
subsampling and horizontal pixel binning for high-speed 12-bit output;
the bandwidth required for 4x4-binned 60 fps PDAF readout is
comparable to the full 100 MP readout already sustained at the
shutter event, suggesting the sensor side is not the constraint.

**Two specific sensor-architecture uncertainties I cannot resolve from
outside the camera.** Both affect how literally the simulation's
numbers should be taken:

(a) *Does the IMX461 preserve PDAF sub-aperture phase signal under
its 4x4 imaging binning mode?* The IMX461 product flyer published by
Sony Semiconductor Solutions confirms binning capability:
"16-bit digital output [enables] 102 MP still mode at 2.7 fps. In
addition, vertical sub-sampling binning and horizontal pixel binning
realize high-speed 12-bit digital output for shooting moving picture."
This documents the binning mode is intended for *moving-picture
imaging output*; it does not describe what happens to PDAF pixels
during that mode. PDAF-in-binned-mode behaviour is not in the public
flyer.

The existence of US 11523071 *"Disparity-Preserving Binning for Phase
Detection Autofocus"* (USPTO patent) is itself evidence that naive
binning destroys PDAF phase signal — that is precisely why the patent
exists, to describe a specific pixel-readout architecture that
preserves disparity through binning. Whether IMX461 implements the
techniques described in that or a comparable patent is not publicly
documented.

My simulation assumes PDAF rows can be read at native 3.76 µm pitch
during AF half-press, decoupled from any imaging binning the EVF
stream uses. If this assumption is incorrect on IMX461 specifically —
if its only binned readout mode is the combined one that averages
PDAF pixels with their neighbours — the binning lever of my proposal
is inapplicable as stated, and the conversation moves to: under what
sensor mode can PDAF be read at full native rate without imaging
binning interfering? This is a question Hasselblad's sensor and
firmware teams can answer authoritatively where I cannot.

(b) *What is the actual ISP-to-decision latency at 60 fps on the X2D?*
The simulation uses 2 frames (~33 ms) as a representative value, but
the real number depends on ISP pipeline scheduling, readout mode, and
firmware. A 1-frame latency would be more forgiving and a 3-frame
latency would degrade temporal-prior performance noticeably. I have
also confirmed that *naive* latency buffering without predict-forward
Kalman compensation breaks high-contrast performance entirely (config
D under naive 2-frame delay scores 0 ± 0 % in-focus in simulation,
documented in the repository). Production-quality latency handling
requires the policy to advance its state estimate forward by N frames
before applying the delayed measurement — the standard
predictive-AF maths used by every modern AF system.

**One general epistemic limit beyond the two above.** The X2D
firmware is closed; observed behaviour is consistent with the
techniques in configuration D being absent, but equally consistent
with them being present and limited elsewhere (tuning, ISP
scheduling, motor driver). The simulation demonstrates these
techniques are *algorithmically feasible* on 294-zone PDAF
architecture — it does not claim knowledge of Hasselblad's internal
codebase. Either reading makes the question worth asking.

These observations are anchored by direct comparison with my own Sony
A7 IV (which on an all-white wall produces a single ~0.7 second hunt
followed by a clear failure indicator), and by published evidence that
Fujifilm achieved substantial AF improvements from GFX 100S to
GFX 100S II without changing PDAF hardware — purely through what
Capture Integration called an "improved predictive AF algorithm."

A note on the X2D II 100C's LiDAR. LiDAR provides direct range and
is a real hardware capability. The specific failure modes I infer
from my X2D observations — a near-focus AF failure consistent with
PDAF correlation-peak broadening at zero defocus, and stochastic
same-scene behaviour consistent with stateless single-frame
decisions — are firmware-level and orthogonal to range sensing.
Configuration D would address them whether LiDAR is present or not.

A related observation. The X2D II 100C's continuous-AF mode is
currently supported on only seven lenses (XCD 25 V, 28 P, 38 V,
55 V, 75 P, 90 V, and 35-100 E), and only in leaf-shutter mode.
Hasselblad's own product communication attributes the limitation to
older XCD focus modules being unable to track quickly enough. This
is a useful data point: even on the body that ships AF-C, the
binding constraint is not the body's silicon but the mechanical
focusing layer of older lenses. The firmware-layer levers I describe
above are independent of that mechanical limitation and would lift
performance wherever the underlying lens can move at the required
rate.

(A side note, offered as informal signal rather than evidence.)
An owner in an Asian medium-format community recently published a
hands-on side-by-side comparison of the X2D, X2D II, GFX 100S, and
GFX 100 II. Their report observes that the X2D II's AF, while
"noticeably faster" than the original X2D, still appears slower than
the GFX 100S — a 2021 body with neither LiDAR nor a deep-learning
AF accelerator. This is one user's experience, not a controlled
benchmark, and the pattern would need broader corroboration before
being load-bearing. I mention it only because, if it is borne out,
it would point at the firmware layer rather than the hardware as
the binding AF-speed constraint — which is precisely what the rest
of this letter argues from the simulation side.

A note connecting this back to my Letter 1 AF-C question. The
components in configuration D — a Kalman temporal prior over focus
position, multi-zone confidence aggregation, and a higher AF
decision-loop framerate — are the same building blocks any
continuous-AF (AF-C) implementation requires. The repository also
contains a `V4Policy` (in `pdaf_sim/policy2d.py`) that adds a 2-D
subject bounding-box Kalman tracker on top of these, demonstrating
that subject persistence across brief occlusions and zone-to-zone
subject motion are likewise tractable on the simulated 294-zone PDAF
architecture. I am not suggesting these specific algorithms are what
should ship; only that the algorithmic question "can AF-C be done on
X2D-class hardware" appears to be answered affirmatively at the
software layer. If your eventual response to Letter 1 is that AF-C
is hardware-limited, this work would refocus the question on
*which* hardware layer specifically constrains it (ISP scheduling,
lens motor driver loop, bus bandwidth) so that the conversation
becomes specific rather than categorical. If your response is that
AF-C is a policy decision rather than a hardware ceiling, then the
two letters together describe one consistent technical proposal
rather than two separate asks.

My question remains the same as in Letter 1, only more specific:

**Is there a path under which firmware-level AF improvements to the
X2D 100C could be considered — whether as a paid capability upgrade,
a routine firmware revision, or a hardware-limitation reply that
honestly closes the topic?**

I am not asking for a position, and I am not asking for my code to
be adopted. I am asking for something simpler — acknowledgement that
the question is worth answering. A 20-year-old who spent his weekend
building this simulation instead of doing anything else did so because
this brand genuinely matters to him. That kind of engagement is rare,
and I would simply value knowing it was seen.

I do not expect a quick response; please take the time you need. I
plan to continue documenting findings on the public repository in the
same calm and non-confrontational tone as this letter, regardless of
how this conversation develops.

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

在技术内容之前,我想先说清楚一件事。X2D 100C 仍然在为我产生其他
任何相机都无法做出的影像。base ISO 下的 HNCS 渲染、混合光线下我能
拿到的动态范围、XCD 55V 对焦环的机械手感、leaf shutter 的安静 —
这些对我来说都不是问题。这封信不是关于一台我后悔购买的相机。**正
是因为 X2D 在其他一切上都对得起它的位置**,AF 行为才显得突出 —
它是整个体验里唯一跟其他部分不匹配的一块。这就是为什么我愿意把自己
的时间投入进去,也是为什么我认为这件事值得你们花时间。

第一封信之后的几天里,我系统地直接观察了自己的 X2D 100C(固件 4.2.0,
XCD 2,5/55V),搭建了一个开源的 PDAF 自动对焦决策策略仿真 testbench,
并把发现跟公开 review、专利文献交叉验证。项目在:

  https://github.com/Konamill-bot/x2d-pdaf-sim

我想坦白说明它**是什么**和**不是什么**。它是一个**公开**、**可复现**、
**自包含**的研究,使用合成场景和薄透镜光学模型。它不是对 Hasselblad
内部实现的任何 claim,也不是要求你们采纳我的代码。它的存在是为了
任何可能的技术对话都从同一套定义出发。

**两个我从外部无法确认的 sensor 架构不确定性**,两者都影响仿真数字
应该被字面理解到什么程度:

(a) *IMX461 在其 4x4 imaging binning 模式下是否保留 PDAF sub-aperture
相位信号?* Sony 半导体的 IMX461 product flyer 确认了 binning 能力:
「16-bit 数字输出 [使] 102 MP 静态模式达 2.7 fps 读取速度。此外,
垂直子采样 binning 和水平像素 binning 实现高速 12-bit 数字输出用于
动态影像拍摄。」这记载了 binning 模式的用途是**动态影像 imaging
输出**;它**没**描述该模式下 PDAF 像素的状态。PDAF-在-binned-模式
的行为不在公开 flyer 内。

US 11523071 *"Disparity-Preserving Binning for Phase Detection
Autofocus"* (USPTO 专利)的存在本身就是证据 — naive binning 会破坏
PDAF 相位信号,这正是该专利存在的原因:描述一种保留 disparity 通过
binning 的特定像素读取架构。IMX461 是否实现了该专利或类似专利描述
的技术,**公开资料中没有记载**。

我的仿真假设 PDAF rows 在 AF 半按时能以 native 3.76μm pitch 读出,
跟 EVF 用的 imaging binning 解耦。如果这个假设对 IMX461 specifically
不成立 — 如果它唯一的 binned 模式就是把 PDAF 像素跟邻居平均的
combined 模式 — 我提案的 binning lever 就不适用,对话需要转向:
在哪种 sensor 模式下 PDAF 能以 full native rate 读出而不被 imaging
binning 干扰?这是 Hasselblad 的 sensor 团队和 firmware 团队能权威
回答的问题,我从外部做不到。

(b) *X2D 在 60 fps 下 ISP 到决策的实际 latency 是多少?* 仿真用 2
帧(~33 ms)作为代表值,但真实数字取决于 ISP pipeline 排程、readout
模式、firmware。1 帧 latency 会更宽容,3 帧 latency 会显著降低时间
先验性能。我也确认了:**没有 predict-forward Kalman 补偿的 naive
latency buffering 会完全破坏高对比性能**(配置 D 在 naive 2 帧延迟下
仿真得分 0 ± 0 % in-focus,记录在 repo 里)。production-quality
的 latency 处理需要 policy 在应用延迟测量前把状态预测前推 N 帧 —
现代 AF 系统都用的标准 predictive-AF 算法。

**除上述两点之外的一个 general 认知限制**:X2D 固件加密,观察到的
行为既可能意味着 configuration D 的技术真的缺失,也可能意味着这些
技术已经存在但被别处限制(参数调校、ISP 排程、马达驱动)。仿真证明
这些技术在 294-zone PDAF 架构上**算法上可行**,不 claim 知道 Hasselblad
内部代码状态。无论哪种解读,问题都值得问。

报告基于 1000 seed 平均的完整 lever-by-lever 实验
(`scripts/run_imx461_stats.py`,`out/imx461_full_stack_metrics.png`)
最重要的发现:**在低对比和高对比场景都可靠获胜的组合,是 sensor
binning + Kalman 时间先验 + multi-zone 信任度聚合(配置 D)**。
(关于 baseline 评分及其与 X2D 真实行为的 cross-validation,
见下方 high-contrast 表后段。)

所有下方数字来自基于 Sony IMX461 真实架构的仿真:21×14 = 294 PDAF
zones uniformly tile 在有效区,每 zone 用 native 3.76μm pitch 做
phase correlation(PDAF rows 不能 binning,否则 sub-aperture 相位
信号被销毁),multi-zone 配置查询主体最近 5 个 zones 并 confidence-
weighted 聚合。Bar chart 在 `out/imx461_full_stack_metrics.png`。
1000 seeds,mean SEM ~0.5 %。

低对比静态目标(in-focus % = 镜头位置在 0.3mm 容差内的帧比例):

| 配置                                            | in-focus %   |
|-------------------------------------------------|--------------|
| A. baseline(stateless, 15 fps, 单 zone)        | 0.0 ± 0      |
| B. + 4x4 binning(60 fps)单独                    | 0.0 ± 0      |
| C. + Kalman 时间先验(单 zone)                  | 5.2 ± 11.3   |
| **D. + multi-zone 聚合(最近 5 zones)**          | **47.1 ± 35.4** |
| E. + deadband + PID + CDAF fusion(V3 stack)    | 36.6 ± 42.2  |

高对比静态目标(同 1000 seeds):

| 配置                                            | in-focus %     |
|-------------------------------------------------|----------------|
| A. baseline                                     | **64.6 ± 42.5** |
| C. + Kalman                                     | 98.2 ± 0.9     |
| **D. + multi-zone**                             | **98.5 ± 0.4** |
| E. + V3                                         | 0.8 ± 8.8(当前坏,见下文) |

四个非显然 findings:

1. **AF 读出帧率单独提高(配置 B)无效,反而更糟**。没有时间先验
   去积分测量,15 → 60 fps 只是把单帧低置信度决策的频率翻 4 倍。
   镜头无效行程从 5.8mm 涨到 23.8mm。binning 和时间先验必须配对。

2. **Configuration D 是推荐的目标**。Binning + Kalman + 最近 5 zone
   multi-zone 聚合产生低对比 47 ± 35 %,高对比 98.5 ± 0.4 %(基于
   1000 独立 seed 在 294-zone IMX461 仿真上,mean SEM 约 0.5 %)。
   计算开销在现代 application-class processor 上是微秒级。更精确
   地说:**configuration D 不是更好 AF-S 的提案。它本身就是一个
   working AF-C loop — 一个 Kalman 滤波、multi-zone、高帧率的决策
   系统,跨帧持续追踪焦点。** 这个仿真和机内 AF-C 实现之间唯一的
   差别,就是 firmware 层是否启用它。

3. **Multi-zone 聚合在低对比上是必须,不是 optional**。Configuration
   C(没有 multi-zone 的 Kalman)在低对比上只有 5 ± 11 % in-focus —
   基本失败。加上最近 5 zone confidence-weighted 聚合把它提升到
   47 ± 35 %。这个 9× 提升是矩阵里最大的单一改进。高对比上 C 和 D
   统计等价(98.2 ± 0.9 vs 98.5 ± 0.4),所以 multi-zone 在简单
   场景上免费,困难场景上必需 — 是 dominant strategy。

4. **完整 V3 stack(配置 E)目前不推荐**。在 294-zone IMX461 仿真
   上 E 得分低对比 37 ± 42 %,高对比 1 ± 9 %。之前在 single-strip
   仿真上 work 的 deadband / PID 参数,跟 multi-zone 聚合 confidence
   输入交互不良;repo 里持续 investigate。这里列出来是为了透明,
   而不是作为推荐。

我想特别指出高对比表里的一个数字:**baseline 评分是 65 ± 43 %**,
意味着当前的单帧 PSR 阈值策略在大约三分之二的 seed 上成功,其余
灾难性失败 — 即使在高对比场景下,理应轻松成功的情况。这个统计
特征跟我在自己 X2D 上直接观察到的「同场景半按多次,有时立即锁
有时 hunting」吻合。仿真从第一性原理(单帧 PSR 阈值 + 噪声 PDAF
相关)独立复现这个 stochasticity — 是整个研究里我的合成模型和
真实相机行为唯一一次独立 cross-validate 的点。这给我对模型其他
预测的质性方向有 modest confidence。

第一个 lever 的两个 conditional caveat 仍然适用:ISP 排程和 sensor
到 SoC 的内部 bus 带宽是 Hasselblad 内部才能测的,所以配置 D 也是
陈述为 *if* AF loop 能通过 PDAF channel 配置跑到 60 fps,*then*
上述结果成立。X2D 的 sensor 是 Sony IMX461,其 datasheet 明确记载
了 vertical subsampling 和 horizontal pixel binning 用于 high-speed
12-bit 输出的支持;4x4 binned 60 fps PDAF readout 所需带宽,跟
shutter event 时已经稳定支持的 full 100 MP readout 带宽相当,
意味着 sensor 侧不是约束。

这些观察以我自己的 Sony A7 IV 直接对比锚定(全白墙场景上 ~0.7 秒
单次 hunt 后明确失败指示),以及 Fujifilm 从 GFX 100S 到 100S II
在不更换 PDAF 硬件的前提下大幅改进 AF 的公开证据(Capture
Integration 称之为 "improved predictive AF algorithm")。

关于 X2D II 100C 的 LiDAR:LiDAR 提供直接测距,是真实硬件能力。
我从 X2D 观察推断的失败模式(近焦点 PSR confidence 下降、单帧无状态
决策的随机性)是 firmware-level,跟测距正交。configuration D 不论
LiDAR 在不在都能解决它们。

另一个相关观察:X2D II 100C 的 continuous-AF 模式目前只支持七颗
镜头(XCD 25 V, 28 P, 38 V, 55 V, 75 P, 90 V, 35-100 E),且仅在
leaf shutter 模式下工作。Hasselblad 自己的产品说明把这个限制归因于
旧 XCD focus 模块无法跟得够快。这是个有用的数据点:即使在出 AF-C
的机身上,binding constraint 也不是机身硅片,而是旧镜头的机械对焦
层。上面描述的 firmware 层 lever 跟这个机械限制无关,在底层镜头能
以所需速率移动的地方,它们都能提升性能。

(以下作为非正式信号附记,不作为证据。)亚洲一个 medium-format 社群的
用家最近发布了 X2D、X2D II、GFX 100S、GFX 100 II 四机身手上 side-by-
side 对比,报告 X2D II 的 AF 虽然"明显快了"过原 X2D,仍然比 GFX 100S
(一台 2021 年、无 LiDAR、无 deep-learning AF accelerator 的机身)
慢。这是单一用户的经验,不是 controlled benchmark,需要更广泛印证才能
作为 load-bearing 论据。我提到它只因为:如果这个 pattern 被印证,它指向
binding AF-speed constraint 是 firmware 层而不是硬件层 — 正是这封信
从仿真侧 argue 的同一个结论。

连接到 Letter 1 的 AF-C 问题:configuration D 用的元件(焦点位置
Kalman 时间先验、multi-zone 信任度聚合、更高的 AF 决策帧率)正是
任何 continuous-AF (AF-C) 实现所需要的基础构件。仓库里还有一个
`V4Policy`(在 `pdaf_sim/policy2d.py`),在这些之上加了 2D 主体
bounding-box Kalman tracker,演示了 subject 跨遮挡持续和跨 zone
运动追踪在仿真的 294-zone PDAF 架构上也是可处理的。我不是建议你们
应该 ship 这些具体算法;只是想说算法层面的「AF-C 能不能在 X2D 级
硬件上做」这个问题看起来在软件层是肯定的。如果你们对 Letter 1 的
回应是 AF-C 受硬件限制,这份工作能把问题精确化到**具体哪一层硬件**
(ISP 排程、镜头马达驱动 loop、bus 带宽);如果你们的回应是 AF-C
是政策决定不是硬件天花板,那两封信合起来描述的就是一个一致的技术
提案,而不是两件分开的事。

我的问题跟 Letter 1 一样,只是更具体:

**有没有一条路径,X2D 100C 的 firmware-level AF 改进能被考虑 ——
不论是付费 capability upgrade、routine firmware revision、还是
诚实关闭话题的"硬件限制"回复?**

我不是在要一个立场,也不是在要你们采纳我的代码。我在要的是更简单的
东西 — 承认这个问题值得回答。一个 20 岁的人把周末花在搭这个仿真
而不是做任何别的事,是因为这个品牌对他真的 matter。这种 engagement
是罕见的,我只想知道它被看见了。

我不期望快速回复,请按你们需要的时间来。无论这个对话怎么发展,我
都会以与本信相同的冷静、不对抗的方式继续在公开仓库记录发现。

再次感谢你们的时间。

Chan Kam Chi 敬上

---

## When to send

Wait until Hasselblad responds to Letter 1, or until 10 business days
have passed from their acknowledgement -- whichever comes first.
Sending too early signals impatience.

When you do send, the subject line should reference Letter 1 explicitly
(e.g. "Follow-up to AF-C inquiry") so it routes to the same person.
