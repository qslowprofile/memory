# Retrieval Protocol

本文档描述 `openclaw-memory-knowledge` 在 P0 升级后的显式检索协议。

目标不是替代 OpenClaw 原生 `memory search`，而是给 Agent 一个稳定的“先看哪层、何时停止、何时继续展开”的读取顺序。

## 1. 默认顺序

默认读取顺序如下：

1. `layers/profile_snapshot.json`
2. `layers/preferences_snapshot.json`
3. `layers/retrieval-hints.json`
4. `layers/l1_overview.jsonl`
5. `layers/l2_records.jsonl`

这对应 `layers/retrieval_protocol.json` 的 `default_flow`。

## 2. 何时优先看 Snapshot

优先读取 snapshot 的问题：

- “这个用户是谁”
- “当前身份、角色、地区、时区是什么”
- “偏好、喜欢、不喜欢、常用工具是什么”

如果 snapshot 已经命中所需字段，应停止，不继续展开 L2。

## 3. 何时停在 L1

以下场景先看 `retrieval-hints` 和 `l1_overview`：

- 想知道应该路由到哪个 bucket
- 想看某类记忆最近是否活跃
- 想先拿关键词、热点 bucket、top entities/tag，再决定是否深挖

如果 bucket 摘要已经足够回答“去哪里找”，应停在 L1。

## 4. 何时打开 L2

只有在以下场景才建议打开 `l2_records.jsonl`：

- 需要原文证据
- 需要 `source_path / locator / section_title`
- 需要核对版本、TTL、heat、trust_tier
- 需要做冲突裁决或人工排错

## 5. Bucket 入口建议

- `memory.profile`：`profile_snapshot -> l2_records`
- `memory.preferences`：`preferences_snapshot -> l2_records`
- `memory.events`：`retrieval-hints -> l1_overview -> l2_records`
- `memory.agent_skill`：`retrieval-hints -> l1_overview -> l2_records`
- `memory.working`：`l2_records`
- `knowledge.facts`：`retrieval-hints -> l1_overview -> l2_records`
- `knowledge.procedures`：`retrieval-hints -> l1_overview -> l2_records`
- `knowledge.references`：`retrieval-hints -> l1_overview -> l2_records`

## 6. 与原生 OpenClaw 检索的关系

本协议不替代 OpenClaw 原生向量/全文混合检索。

两者关系是：

- OpenClaw `memory search`：负责召回
- 本协议：负责“召回后怎么看层次文件、什么时候停止展开”

因此这层更接近 progressive disclosure，而不是新的检索引擎。
