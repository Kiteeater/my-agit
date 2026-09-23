# 架构

本文只记分层纪律和 Harness 插槽。产品阶段仍以 [ROADMAP.md](ROADMAP.md) 为准。

## 分层

依赖方向是 service → biz → data → domain。domain 不依赖其他层。bench 和 judge 在门禁旁边：biz.release 调用它们拿分数，它们不读写 store，也不改 red / black。judge 的接缝是 `Judge.score_harness`；compare 和 gate 不 import 具体厂商。bench 的接缝是 `load_bench(spec)`。`fixture`（默认）与 `hot` 是包内 JSON；其他 spec 若指向一个已存在的文件，则按同一形状解析。biz.release 把得到的 `Bench` 交给 `Judge`，门禁只看返回的分数。

| 层 | 职责 | 在本仓库 |
| --- | --- | --- |
| domain | 纯模型。无 I/O，无门禁规则 | `Project`、`Version`、`Harness`、`ReleaseEvent` |
| data | 唯一持久化：JSON 读写和序列化 | `Store` |
| biz | 业务规则，按能力拆开 | `biz.project` 创建 / 读取 / 鉴权；`biz.version` push / list / get / `version_id_for`；`biz.release` bootstrap / mark red / compare / gate |
| service | 薄适配。解析参数，调用 biz | `service.api`、`service.cli`、`service.demo` |
| bench | 题目和 rubric。`load_bench(spec)` 读内置 fixture、内置 hot 样例，或同构 JSON 文件 | `agit/fixtures.json`、`agit/hot_fixtures.json` |
| judge | 把 harness 打成分数 | `Judge` 协议；实现是 stub、fixed、OpenAI-compatible |

`agit/api.py`、`agit/cli.py`、`agit/demo.py`、`agit/store.py` 是兼容导入。`agit/core.py` 只重新导出上述 biz 函数，并标为 deprecated。入口仍然是 `python -m agit`。

## 分层纪律

- 模型、持久化、业务规则、入口分开。一类变化留在一层。
- service 不做发布决定。data 不写门禁规则。domain 不碰文件和 HTTP。
- biz 按能力分文件，而不是一个模块同时管项目、版本和发布。

## 明确不做

这些不属于当前竖切，也不进本仓库：

- 外部数据库，以及生成式 data repo
- 多服务 remote 客户端
- observability（trace、指标导出等）
- MCP、任务队列、多租户

## Harness 插槽

竖切 ≠ 不留插槽。现役面是 skill、prompt，以及可选的 tool。`Harness.tool` 默认为 `None`，表示这次版本没有这个面。

- 旧 store JSON 只有 `skill` 和 `prompt`。读入时 `tool` 视为缺省。写出时缺省面不写这个键，所以只含 skill / prompt 的版本对象形状不变。
- `version_id` 只哈希已经出现的面。仅 skill + prompt 时，摘要是 skill 的 UTF-8、一个 NUL、prompt 的 UTF-8 的 SHA-256，与插槽出现之前相同。`tool` 有值时，摘要改为带 `skill` / `prompt` / `tool` 标签的字节，避免和旧编码撞车。
- `judge.score_harness` 接受 `Version` 或 `Harness`。打分仍只看 skill 和 prompt。
- CLI：`agit version push` 可选 `--tool-file`。省略则不传 tool，与以前相同。传入则读取文件文本，交给 `push_version(..., tool=...)`。
- HTTP：`POST /v1/projects/{id}/versions` 的请求体可选字符串 `tool`。省略则不传 tool。有值则走同一个 `push_version`。

下一个子面沿同一条缝加：domain 上一个缺省字段，出现了才进入 `version_id` 和 JSON，门禁仍只消费分数。runtime、fallback、mcp、sandbox 现在没有字段，也不在本包实现。
