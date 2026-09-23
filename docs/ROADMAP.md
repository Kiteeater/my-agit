# my-agit 开发指引与路线

本文是开发指引，不是产品宣传。只记录已经锁定的决定、当前 `main` 上的 v0，以及由此推出的阶段。未锁定的选择标为 **Open**。不写日期、指标，也不补主人没有说过的功能。

## 产品一句话

my-agit = 「给 Agent 用的 git」：Agent Harness 版本管理 + 红黑发布门禁。

平台方向：每人 create 自己的 agent 项目，绑定自己的 git 仓库，用 API key 做版本管理。接入面包括 agent harness、任意 provider、自定义 benchmark，以及热门 bench。

版本管理是基础能力。红黑门禁是产品本身：即将上线版对照现役版，同条件评分，过线才发布。

## 和 Reef 的硬差异

这些差异是锁定的，后续阶段不得把它们做回去。

1. **控制面，不是 continual learning 基础设施。** 默认只做 Harness（Prompt / 规则 / Skill，以及后文的 harness 子面）的版本化与上线控制。不是 Reef 式「推理 + 反馈 + 学习 + 投递」一体化。**不做权重训练。**
2. **红黑门禁即产品。** 即将上线版（red）与现役版（black）在同一套条件下对照评分，过线才把 red 提升为 black。评估不是学习闭环里的附属策略。
3. **明确不做。** Trace Distiller / warrant 对接不做。真实线上 case 回收后置，第一阶段不接真实环境。

## 真实用法

一次 PR / 发布可以拆成对 agent 有影响的部分，分别版本化：

| 部分 | 范围 | 状态 |
| --- | --- | --- |
| skill | 技能文本 | v0 已版本化 |
| prompt | 提示文本 | v0 已版本化 |
| harness | tool、runtime、fallback、mcp、sandbox 等子面 | 尚未版本化 |

发布路径：准备候选版本，标成 red，与当前 black 在同一 benchmark、同一 rubric、同一 judge 下评分，再决定是否发布。`compare` 只打分，不改现役；`gate` 才决定是否提升。

线上 case 回收依赖真实环境。第一阶段先做 benchmark：定好 rubric，再用 LLM-as-judge。离线 stub judge 只为了 demo 和 CI 能在没有 API key 时跑通同一条门禁。

## 开发规范（长期有效）

先竖向把最简单的路径跑通，再在这条最简单、最朴实的架构上做高内聚、低耦合的开发。目录和依赖方向见 [ARCHITECTURE.md](ARCHITECTURE.md)。

- **一次只扩一条可验收的竖切。** 不做大而全起步。一条竖切要能用现有 CLI 或 HTTP 走完，并有测试。
- **竖切 ≠ 不留插槽。** 当前竖切可以只跑 skill + prompt，但版本模型必须能挂上下一个 harness 子面。缺省插槽不改变已有数据、version_id 和门禁语义；某个面出现了，才进入内容哈希。不要等实现子面时再拆掉写死的两个字段。
- **高内聚。** 一类变化留在一个层里：模型在 domain，持久化在 data，项目 / 版本 / 发布规则在 biz，题目与 rubric 在 bench，评分在 judge，入口只做 CLI / HTTP / demo。
- **低耦合。** 新的 harness 子面、provider、benchmark 从现有接缝接入（Harness 插槽、bench、judge）。门禁只消费分数，不认识某一家模型或某一个 bench 的内部细节。不要为了新能力复制第二条发布路径。
- **朴实架构优先。** 在竖切证明价值之前，不引入多租户平台、任务队列、权限体系或其他 SaaS 复杂度。
- **范围守门。** 训练、Trace Distiller、线上流量影子，都不作为「顺便」做进当前竖切。

## 当前状态（v0，`main`）

已经交付的竖切：

- 项目：create、绑定 git remote URL、签发 API key。URL 存在项目上；v0 不 clone 该仓库。
- 版本对象：现役面仍是 **skill 与 prompt**。版本 id 在只有这两面时是这两段文本的内容哈希；同一对再次 push 返回已有版本。`Harness.tool` 是空插槽：缺省不写入 JSON，也不改变 version_id。tool 的版本化行为还没做。
- 红黑与门禁：`release black` 只引导第一个现役版本，之后 black 只能经 `gate` 改变。内置 fixture bench + rubric；judge 为 stub，或 OpenAI-compatible chat completion（LLM-as-judge）。候选分必须严格更高，且领先不少于配置的 margin，才提升；否则 black 不动，候选保持 red。
- 入口：CLI、HTTP、`agit demo`、单元测试。

v0 明确还没有：tool 的版本化行为（只有空插槽），以及 runtime / fallback / mcp / sandbox 的字段和版本化；OpenAI-compatible 以外的 provider；自定义 bench 或热门 bench；线上 case 回收。

## 阶段

阶段顺序按锁定范围推出。没有排期。某一阶段内部尚未锁定的选择单独标 **Open**。

### Done / v0 — skill + prompt，fixture bench 上的红黑门禁

见上一节。后续阶段在这条竖切上加东西，不重写门禁语义。

### Next — harness 子面的最小子集

把版本对象从 skill + prompt 扩到 harness 的一个子面，使一次发布可以带上对 agent 有影响的 harness 变更，并仍走同一条红黑对照。模型上已经留了 `Harness.tool` 插槽；这一阶段才实现它的版本化行为，不另开一条发布路径。

**建议（非锁定）：先做 tool。** runtime、fallback、mcp、sandbox 后置。先做哪一个之外的子面、各子面的产物形状，均为 **Open**。不要一次把五个子面都版本化。

### 随后 — provider 任意化

在现有 OpenAI-compatible judge 之外，做成可接入的 provider 抽象，使门禁不绑死在一家 chat completion 上。

抽象边界、配置方式、除当前这一家以外先接哪一家，均为 **Open**。stub judge 继续承担无 key 的 demo 与 CI。

### 随后 — benchmark 插件

在内置 fixture bench 之外，支持自定义 bench，并至少接入 1 个热门 bench。rubric 仍由使用方定好，评分仍走 LLM-as-judge（或 stub 这类离线对照）。

热门 bench 选哪一个、自定义 bench 的装载格式，均为 **Open**。不在此阶段改成线上 case。

### 随后 — 平台多项目

把「每人 create 自己的 agent 项目、绑定自己的 git 仓库、用 API key 做版本管理」做成更完整的产品面。v0 已经有单项目的 create、URL 绑定和 API key；这一阶段是在同一套朴实架构上把多项目使用铺开。

账号、项目列表、仓库绑定是否超出「存 URL」等产品细节，均为 **Open**。不借此引入训练面或 Trace Distiller。

### Later — 线上 case 回收

在真实环境里回收线上 case，作为 benchmark 之后的评估来源。前置是：rubric 与门禁已经在 benchmark 上跑通。

环境形态、回收什么、如何进入现有 bench / judge，均为 **Open**。此阶段仍然不做权重训练，不做 Trace Distiller / warrant。

## 非目标

以下事项不在路线内。实现时如果一条改动必须依赖它们，就停下来，而不是把范围扩大。

- 权重训练，以及任何「版本管理顺便更新模型权重」的路径。
- 照搬 Reef 的 Serve / Observe / Grow / Commit 全栈。
- Trace Distiller，以及 warrant 对接。
- 在竖切价值出现之前堆 SaaS 复杂度（为了像平台而加的控制台、计费、组织权限等）。
- 把评估重新做成学习闭环的附属策略。红黑门禁保持为发布决定。
