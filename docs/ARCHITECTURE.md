# 架构

本文只记分层纪律和 Harness 插槽。产品阶段仍以 [ROADMAP.md](ROADMAP.md) 为准。

## 分层

依赖方向是 service → biz → data → domain。domain 不依赖其他层。bench 和 judge 在门禁旁边：biz.release 调用它们拿分数，它们不读写 store，也不改 red / black。

| 层 | 职责 | 在本仓库 |
| --- | --- | --- |
| domain | 纯模型。无 I/O，无门禁规则 | `Project`、`Version`、`Harness`、`ReleaseEvent` |
| data | 唯一持久化：JSON 读写和序列化 | `Store` |
| biz | 业务规则，按能力拆开 | `biz.project` 创建 / 读取 / 鉴权；`biz.version` push / list / get / `version_id_for`；`biz.release` bootstrap / mark red / compare / gate |
| service | 薄适配。解析参数，调用 biz | `service.api`、`service.cli`、`service.demo` |
| bench | 题目和 rubric | `agit/fixtures.json` |
| judge | 把 harness 打成分数 | stub，或 OpenAI-compatible chat completion |

`agit/api.py`、`agit/cli.py`、`agit/demo.py`、`agit/store.py` 是兼容导入。`agit/core.py` 只重新导出上述 biz 函数，并标为 deprecated。入口仍然是 `python -m agit`。

## 学 macaron-agent 什么

对照 `macaron-agent/src` 的 domain / data / biz / service，只学分层纪律：

- 模型、持久化、业务规则、入口分开。一类变化留在一层。
- service 不做发布决定。data 不写门禁规则。domain 不碰文件和 HTTP。
- biz 按能力分文件，而不是一个模块同时管项目、版本和发布。

## 故意不抄什么

macaron-agent 里这些东西不属于这条竖切，也不进本仓库：

- Mongo，以及 decorator 生成的 data repo
- remote 多服务客户端
- observability（langfuse、trace、指标）
- MCP、任务队列、多租户

## Harness 插槽

竖切 ≠ 不留插槽。现役面仍是 skill + prompt。`Harness.tool` 默认为 `None`，表示这个面还没出现。

- 旧 store JSON 只有 `skill` 和 `prompt`。读入时 `tool` 视为缺省。写出时缺省面不写这个键，所以只含 skill / prompt 的版本对象形状不变。
- `version_id` 只哈希已经出现的面。仅 skill + prompt 时，摘要是 skill 的 UTF-8、一个 NUL、prompt 的 UTF-8 的 SHA-256，与插槽出现之前相同。`tool` 有值时，摘要改为带 `skill` / `prompt` / `tool` 标签的字节，避免和旧编码撞车。
- `judge.score_harness` 接受 `Version` 或 `Harness`。打分仍只看 skill 和 prompt。
- CLI 和 HTTP 的 push 仍然只收 skill、prompt、message。`push_version` 另有可选的 `tool` 参数，本包不把它暴露成参数或请求字段。

下一个子面沿同一条缝加：domain 上一个缺省字段，出现了才进入 `version_id` 和 JSON，门禁仍只消费分数。runtime、fallback、mcp、sandbox 现在没有字段，也不在本包实现。
