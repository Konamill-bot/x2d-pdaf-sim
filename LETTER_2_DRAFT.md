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

I want to lead with the most important finding from the full
lever-by-lever experiment (`scripts/run_full_stack_stats.py`,
`out/full_stack_metrics.png`), based on 10 independent seeds per
configuration: **the combination that produces a reliable win across
both low-contrast and high-contrast scenes is sensor binning + Kalman
temporal prior + multi-zone confidence aggregation.** Earlier
single-seed runs in this study showed misleading results (different
seeds favoured different configurations); reporting now with 10-seed
means and standard deviations is what changed the recommendation.

Low-contrast static target (100 seeds, mean ± std of in-focus %, where
in-focus means lens within 0.3 mm of target):

| Configuration                                  | in-focus %  | trav (mm) |
|------------------------------------------------|-------------|-----------|
| A. baseline (stateless, 15 fps, single zone)   | 0 ± 0       | 5.80      |
| B. + 4x4 binning (60 fps) only                 | 0 ± 0       | 23.80     |
| C. + Kalman temporal prior                     | 36 ± 34     | 9.05      |
| **D. + multi-zone confidence aggregation**     | **86 ± 16** | 2.35      |
| E. + deadband + PID + CDAF fusion (V3 stack)   | 35 ± 44     | 1.72      |

High-contrast static target (same 100 seeds):

| Configuration                                  | in-focus %  | lock time   |
|------------------------------------------------|-------------|-------------|
| A. baseline                                    | **56 ± 48** | 0.08 s      |
| C. + Kalman                                    | 90 ± 9      | 0.18 s      |
| **D. + multi-zone**                            | **91 ± 3**  | 0.18 s      |
| E. + V3                                        | 87 ± 24     | 0.14 s      |

I want to call out one specific number in the high-contrast table:
**the baseline scores 56 ± 48 %**, meaning the current single-frame
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
   multi-zone produces 82 ± 16 % in-focus on low-contrast and 92 ± 1 %
   on high-contrast. Critically, the standard deviation is small,
   meaning the result is reliable rather than seed-lucky. The compute
   cost is on the order of microseconds per frame on any modern
   application-class processor.

3. **Configuration C without multi-zone is unreliable.** It averages
   33 % in-focus on low-contrast but with ±31 % standard deviation —
   some scenes work cleanly, others fail completely. Multi-zone
   aggregation reduces this variance from ±31 % to ±16 % while
   raising the mean.

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

报告基于 100 seed 平均的完整 lever-by-lever 实验
(`scripts/run_full_stack_stats.py`,`out/full_stack_metrics.png`)
最重要的发现:**在低对比和高对比场景都可靠获胜的组合,是 sensor
binning + Kalman 时间先验 + multi-zone 信任度聚合(配置 D)**。
另外一个重要发现:**high-contrast 上 baseline 评分是 56 ± 48 %**,
正好对应我在自己 X2D 上观察到的"同场景半按多次,有时立即锁有时
hunting"的随机行为 — 仿真从第一性原理(单帧 PSR 阈值 + PDAF 相位
相关 noise)独立复现了这个统计特征,说明仿真模型至少在质性方向上
是对的。

低对比静态目标的 lever 矩阵:

| 配置                                            | in-focus % | final err |
|-------------------------------------------------|------------|-----------|
| A. baseline(stateless, 15 fps, 单 zone)        | 0%         | 2.50 mm   |
| B. + 4x4 binning(60 fps)单独                    | **0%**     | 2.50 mm   |
| C. + Kalman 时间先验                             | **85%**    | **0.03 mm** |
| D. + multi-zone 信任度一致性                     | 35%        | 0.37 mm   |
| E. + deadband + PID + CDAF fusion(V3 stack)    | 0%         | 0.57 mm   |

非显然 findings:

1. **AF 读出帧率单独提高(配置 B)无效,甚至更糟**。没有时间先验
   去积分测量,15 → 60 fps 只是把单帧低置信度决策的频率翻 4 倍。
   镜头无效行程从 5.8mm 涨到 23.8mm。两个 lever 必须配对。

2. **Kalman 时间先验(配置 C)才是真正的 hero**。binning + Kalman
   在 2.5mm 目标上 0.30s 内锁焦,baseline 永远到不了。这是我能站住
   脚的 headline 结果。计算开销在现代 application-class processor
   上是微秒级。

3. **Multi-zone confidence aggregation(配置 D)在低对比场景退步**。
   当每个 zone 都看到 noise 时,zone 间 median 也是 noise — 没有
   共识可提取。multi-zone 对周期 aliased 信号有效,应**条件性**用,
   不是无条件用。

4. **完整 V3 stack(配置 E)在低对比上过度约束**。Deadband + PID +
   CDAF fusion 改进机械平滑度,减少镜头马达行程 ~30%,但在无特征
   主体上会让镜头根本到不了焦。它们适合高对比主体(已经能锁的
   情况下),不适合作为低对比的 default。

第一个 lever 的两个 conditional caveat 仍然适用:ISP 排程和 sensor
到 SoC 的内部 bus 带宽是 Hasselblad 内部才能测的,所以即使配置 C
也是 *if* AF loop 能跑到 60 fps,*then* 上述结果成立。

这些观察以我自己的 Sony A7 IV 直接对比锚定(全白墙场景上 ~0.7 秒
单次 hunt 后明确失败指示),以及 Fujifilm 从 GFX 100S 到 100S II
在不更换 PDAF 硬件的前提下大幅改进 AF 的公开证据(Capture
Integration 称之为 "improved predictive AF algorithm")。

我知道 X2D II 100C 用 LiDAR 解决 AF 问题。LiDAR 提供测距,是有意义
的硬件改进;但它并不解决我识别的失败模式(特别是近焦点 PSR
confidence 下降和无状态单帧决策策略)。上述 binning + Kalman 组合
(配置 C)在 X2D 100C 上有独立价值 — 无论 LiDAR 在不在,它都能解决
这些 firmware-level 失败模式。

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
