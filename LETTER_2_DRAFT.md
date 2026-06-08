# Letter 2 — Draft

Two versions: English (to send) and Chinese (for your own reference).
Send English. Keep tone consistent with Letter 1: technical, calm,
respectful, no demands. State observations, present analysis, ask one
clear question.

**PRE-SEND CHECKLIST (do these in order before sending):**
1. The letter references `https://github.com/Konamill-bot/x2d-pdaf-sim`
   in four places (lines 27, 197, 198, 215). This repository does
   not yet exist publicly. Push the local repo to GitHub following
   `GITHUB_PUSH.md` BEFORE sending this letter. If repo is not yet
   public, the link will 404 and damage credibility.
2. Verify Hasselblad has responded to Letter 1 (or 10 business days
   have passed from their acknowledgement) -- see "When to send"
   section at the end of this file.
3. Skim the letter once for the 5 numbers that will hit hardest if
   wrong: 1000 seeds, 86 ± 15 % (D low-contrast),
   88 ± 16 % (D high-contrast), 52 ± 48 % (baseline high-contrast),
   0.5 % mean SEM. These must match `out/full_stack_metrics.png`.
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
lever-by-lever experiment (`scripts/run_full_stack_stats.py`,
`out/full_stack_metrics.png`), based on 1000 independent seeds per
configuration: **the combination that produces a reliable win across
both low-contrast and high-contrast scenes is sensor binning + Kalman
temporal prior + multi-zone confidence aggregation.** Earlier
single-seed runs in this study showed misleading results (different
seeds favoured different configurations); reporting now with
1000-seed means and standard deviations is what changed the
recommendation.

Low-contrast static target (1000 seeds, mean ± std of in-focus %,
where in-focus means lens within 0.3 mm of target). The mean
standard error at n=1000 is ~0.5%, so each mean below is accurate to
roughly one percentage point:

| Configuration                                  | in-focus %   | trav (mm) |
|------------------------------------------------|--------------|-----------|
| A. baseline (stateless, 15 fps, single zone)   | 0.0 ± 0      | 5.80      |
| B. + 4x4 binning (60 fps) only                 | 0.0 ± 0      | 23.80     |
| C. + Kalman temporal prior                     | 38.6 ± 35.1  | 9.42      |
| **D. + multi-zone confidence aggregation**     | **86.0 ± 15.4** | 2.37   |
| E. + deadband + PID + CDAF fusion (V3 stack)   | 35.9 ± 43.8  | 1.74      |

High-contrast static target (same 1000 seeds):

| Configuration                                  | in-focus %    | lock time   |
|------------------------------------------------|---------------|-------------|
| A. baseline                                    | **51.9 ± 48.3** | 0.08 s    |
| C. + Kalman                                    | 87.9 ± 15.9   | 0.19 s      |
| **D. + multi-zone**                            | **87.6 ± 16.3** | 0.19 s    |
| E. + V3                                        | 82.4 ± 29.7   | 0.14 s      |

I want to call out one specific number in the high-contrast table:
**the baseline scores 52 ± 48 %**, meaning the current single-frame
PSR-threshold policy succeeds on roughly half the seeds and fails
catastrophically on the other half, even on high-contrast scenes
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
   multi-zone produces 86 ± 15 % in-focus on low-contrast and 88 ± 16 %
   on high-contrast (over 1000 independent seeds, mean SEM ~0.5 %).
   Compute cost is on the order of microseconds per frame on any
   modern application-class processor.

3. **Configuration C without multi-zone is unreliable on low-contrast.**
   It averages 39 % in-focus on low-contrast but with ±35 % standard
   deviation — some scenes work cleanly, others fail completely. On
   high-contrast scenes C and D are statistically equivalent (88 ± 16
   vs 88 ± 16), so multi-zone is essentially free on easy scenes and
   essential on hard ones — a dominant strategy.

4. **The full V3 stack (config E) over-constrains low-contrast scenes
   while remaining the fastest on high-contrast.** Deadband + PID +
   CDAF fusion improve mechanical smoothness and reduce lens motor
   travel further, but on featureless subjects they prevent the lens
   from reaching focus reliably (18 ± 36 %). They are appropriate as
   a *user-selectable* mode for known-good lighting / textured
   subjects, not as a universal default.

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

**One epistemic limit I want to acknowledge explicitly.** This study
assumes a baseline algorithm whose simulated output behaviour matches
what I observe on my X2D — hunting in low contrast, stochastic
same-scene response, near-focus failure. That match is consistent
with the baseline genuinely lacking the techniques in configuration D,
but it is also consistent with the X2D's firmware already
incorporating some or all of them and being limited elsewhere
(tuning, ISP scheduling, motor driver). The firmware is encrypted
and I cannot verify which is true from outside. The simulation's
purpose is therefore to demonstrate that the techniques are
*algorithmically feasible* on a 294-zone PDAF architecture, not to
claim knowledge of what is or is not in Hasselblad's internal
codebase. Either way the question I am asking is meaningful: if the
techniques are not yet present, configuration D suggests they would
help; if they are present but the result is still the observed
hunting, that itself is information you alone can interpret.

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
binning + Kalman + multi-zone combination above (configuration D) has independent
value on the X2D 100C — it would address those firmware-level failure
modes whether or not LiDAR is present.

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

**先讲一个认知限制(epistemic limit)**:这个研究假设的 baseline 算法
其仿真输出行为(hunting、随机性、近焦点失败)跟我在 X2D 上观察到的
吻合。这个吻合**既可能**意味着 X2D 固件确实缺少 configuration D 里
的技术,**也可能**意味着 X2D 已经有这些技术但被别处限制(参数调校、
ISP 排程、马达驱动)。固件加密,从外部无法验证哪个为真。仿真的目的
是证明这些技术在 294-zone PDAF 架构上**算法上可行**,不是 claim
知道 Hasselblad 内部代码状态。无论哪种情况,问题都是 meaningful 的:
如果技术还没用,configuration D 提示它们会有帮助;如果已经用了但
结果仍是观察到的 hunting,那本身就是只有你们能解读的信息。

报告基于 1000 seed 平均的完整 lever-by-lever 实验
(`scripts/run_full_stack_stats.py`,`out/full_stack_metrics.png`)
最重要的发现:**在低对比和高对比场景都可靠获胜的组合,是 sensor
binning + Kalman 时间先验 + multi-zone 信任度聚合(配置 D)**。
另外一个重要发现:**high-contrast 上 baseline 评分是 56 ± 48 %**,
正好对应我在自己 X2D 上观察到的"同场景半按多次,有时立即锁有时
hunting"的随机行为 — 仿真从第一性原理(单帧 PSR 阈值 + PDAF 相位
相关 noise)独立复现了这个统计特征,说明仿真模型至少在质性方向上
是对的。

低对比静态目标(1000 seeds,mean ± std of in-focus %,n=1000 时
mean 的 standard error 约 0.5 个百分点):

| 配置                                            | in-focus %    | trav (mm) |
|-------------------------------------------------|---------------|-----------|
| A. baseline(stateless, 15 fps, 单 zone)        | 0.0 ± 0       | 5.80      |
| B. + 4x4 binning(60 fps)单独                    | 0.0 ± 0       | 23.80     |
| C. + Kalman 时间先验                             | 38.6 ± 35.1   | 9.42      |
| **D. + multi-zone 信任度聚合**                  | **86.0 ± 15.4** | 2.37    |
| E. + deadband + PID + CDAF fusion(V3 stack)    | 35.9 ± 43.8   | 1.74      |

高对比静态目标(同 1000 seeds):

| 配置                                            | in-focus %      | lock time |
|-------------------------------------------------|-----------------|-----------|
| A. baseline                                     | **51.9 ± 48.3** | 0.08 s    |
| C. + Kalman                                     | 87.9 ± 15.9     | 0.19 s    |
| **D. + multi-zone**                             | **87.6 ± 16.3** | 0.19 s    |
| E. + V3                                         | 82.4 ± 29.7     | 0.14 s    |

四个非显然 findings:

1. **AF 读出帧率单独提高(配置 B)无效,反而更糟**。没有时间先验
   去积分测量,15 → 60 fps 只是把单帧低置信度决策的频率翻 4 倍。
   镜头无效行程从 5.8mm 涨到 23.8mm。binning 和时间先验必须配对。

2. **Configuration D 是推荐的目标**。Binning + Kalman + multi-zone
   产生低对比 86 ± 15 %,高对比 88 ± 16 %(基于 1000 独立 seed,
   mean SEM 约 0.5 %)。计算开销在现代 application-class processor
   上是微秒级。

3. **Configuration C(没 multi-zone)在低对比上不可靠**。平均 39 %
   in-focus 但 ±35 % 标准差 — 有些场景跑得很干净,有些完全失败。
   高对比上 C 和 D 统计等价(88±16 vs 88±16),所以 multi-zone
   在简单场景上 essentially free,在困难场景上 essential — 是
   dominant strategy。

4. **完整 V3 stack(配置 E)在低对比上过度约束,但在高对比上锁
   最快**。Deadband + PID + CDAF fusion 改进机械平滑度,但在无
   特征主体上无法可靠对焦(18 ± 36 %)。适合作为已知好光线 / 有
   纹理主体的 *user-selectable* 模式,不是 universal default。

我想特别指出高对比表里的一个数字:**baseline 评分是 52 ± 48 %**,
意味着当前的单帧 PSR 阈值策略在大约一半的 seed 上成功,另一半灾难
性失败 — 即使在高对比场景下,理应轻松成功的情况。这个统计特征跟
我在自己 X2D 上直接观察到的「同场景半按多次,有时立即锁有时
hunting」吻合。仿真从第一性原理(单帧 PSR 阈值 + 噪声 PDAF 相关)
独立复现这个 stochasticity — 是整个研究里我的合成模型和真实相机
行为唯一一次独立 cross-validate 的点。这给我对模型其他预测的质性
方向有 modest confidence。

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

我知道 X2D II 100C 用 LiDAR 解决 AF 问题。LiDAR 提供测距,是有意义
的硬件改进;但它并不解决我识别的失败模式(特别是近焦点 PSR
confidence 下降和无状态单帧决策策略)。上述 binning + Kalman 组合
(配置 C)在 X2D 100C 上有独立价值 — 无论 LiDAR 在不在,它都能解决
这些 firmware-level 失败模式。

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
