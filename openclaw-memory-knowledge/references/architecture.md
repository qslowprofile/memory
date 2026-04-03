# AdaMem-lite + OpenViking 分层架构（Skill 版）

本 skill 采用“分类学 + 存储工程 + 自修复”组合：

- 分类学：AdaMem 风格分桶（扩展到 8 桶）
- 存储工程：OpenViking 风格 `L0 / L1 / L2` + `viking/...` 目录
- 自修复：Probe -> Verify -> Repair（含 TTL/热度卫生）

并遵循“零外部 embedding 服务”原则：

- 不依赖 localhost 向量服务
- 可直接复用 OpenClaw 原生 memory backend（builtin + hybrid）

## 1) 目标目录结构

```text
target_root/
  viking/
    user/
      memories/
        profile.jsonl
        preferences.jsonl
        events.jsonl
        entities.jsonl
        relations.jsonl
        relation_decisions.jsonl
      knowledge/
        facts.jsonl
        procedures.jsonl
        references.jsonl
    agent/
      skills/
        buffer.jsonl
    session/
      working/
        buffer.jsonl
    archive/
      records.jsonl
  layers/
    l0_abstract.json
    l1_overview.jsonl
    l2_records.jsonl
    profile_snapshot.json
    preferences_snapshot.json
    retrieval-hints.json
    retrieval_protocol.json
  state/
    record_hashes.txt
    processed_files.json
  reports/
    bootstrap_*.json
    ingest_*.json
    rejected_*.jsonl
    policy_skipped_*.jsonl
```

原生 OpenClaw 工作区模式（传 `--workspace-root`）下默认 `target_root`：

- `<workspace_base>/memory/.adaptr-v1`

## 2) 分层定义

- `L2`：原子记录层（包含 `source_path`、`locator`、`version`、`ttl`、`heat`、`last_accessed`）
- `L1`：bucket 摘要层（top tags/entities + sample text）
- `L0`：全局统计层（总量、分布、热点）
- `profile_snapshot`：当前态画像快照（从 `memory.profile` 高置信记录中抽取）
- `preferences_snapshot`：当前态偏好快照（从 `memory.preferences` 高置信记录中抽取）
- `retrieval-hints`：检索提示层（hot buckets + bucket keywords + flash records + relation hints）
- `retrieval_protocol`：显式检索协议（先看哪些层、何时停在 L1、何时打开 L2）

## 2.1) L2 扩展元数据（P0 新增）

每条 L2 记录除了原有字段外，新增 4 个治理字段：

- `memory_function`：`factual | experiential | working`
- `formation_mode`：`bootstrap | ingest | runtime | manual`
- `trust_tier`：`curated | extracted | generated`
- `memory_policy`：`persist | private | ephemeral`

它们不替代现有 8 桶，只是补充“功能类型、形成方式、可信等级、持久化策略”四个治理视角。

## 3) 8 桶分类与路由

- Memory
  - `memory.profile`
  - `memory.preferences`
  - `memory.events`
  - `memory.agent_skill`
  - `memory.working`
- Knowledge
  - `knowledge.facts`
  - `knowledge.procedures`
  - `knowledge.references`

分类链路是三段式：

1. 路径优先（P1）
2. frontmatter / `Category:` 提示（P2）
3. 关键词计分兜底（P3）

## 4) 结构化切块与追溯

- Markdown 文件优先按 `## / ###` section 边界切块
- 每条记录写入 `section_title`（metadata）
- `⚡` 行自动打 `flash` tag，进入 `retrieval-hints.flash_records`

对快照提取额外支持：

- frontmatter 字段优先参与 profile/preferences 快照抽取
- `key: value` 与 `key：value` 都会被识别
- 中文材料中的全角冒号不会丢失 current-state 信息

## 5) 增量 UPDATE 语义

增量 ingest 不再仅 append，而是按“文件级替换”执行：

1. 找到变更文件在 L2 的旧记录并移除
2. 重新抽取该文件的新记录
3. 以 `(source_path, locator)` 递增 `version`
4. 重建 bucket/L0/L1/L2/entities/relations/hash state

结果：同一源记录更新时会替换旧版本，不会无限累积重复历史。

## 6) TTL / 热度 / 归档

- 每条记录入库时自动补齐：
  - `ttl`（如 `7d / 90d / permanent`）
  - `heat`（`hot / warm / cold`）
  - `last_accessed`
- `self_evolve --repair` 新增 TTL 探针：
  - 超过 TTL：降级为 `cold`
  - 超过 `2xTTL`：移入 `viking/archive/records.jsonl`

## 7) 写入质量门控

写入前执行轻量门控：

- 置信度过低拒绝
- 文本过短拒绝
- 纯符号/数字拒绝

拒绝记录写入 `reports/rejected_*.jsonl` 便于审计。
同时在 `rebuild_materialized_views_from_l2()` 重建阶段也会复用门控，清理历史低质量遗留数据。

## 7.1) Memory Policy（P0 新增）

支持两种入口：

- frontmatter / metadata：`memory_policy: private|ephemeral|persist`
- inline 标签：`<private>...</private>`

规则如下：

- `private`：不进入长期层，不写入 L2 / graph / retrieval layers
- `ephemeral`：允许抽取，但会在入库时强制路由到 `memory.working`
- `persist`：正常入库

所有被 policy 拦截的记录都会写入 `reports/policy_skipped_*.jsonl`，便于审计。

## 8) 自进化闭环

`scripts/self_evolve.py --repair` 会做：

- L0/L1/L2 一致性检查
- bucket 与 L2 对齐检查
- L2 去重检查
- 图谱边可用性检查
- hash-state 覆盖检查
- TTL/热度卫生检查
- 图谱语义质量检查（弱关系占比，自动重算 entities/relations）
- 派生层存在性检查（`profile_snapshot` / `preferences_snapshot` / `retrieval_protocol`）
- `memory_policy` 执行检查（确保 `private` 不残留在 L2）

修复策略统一为“以 L2 为准重建物化视图”，避免局部修补导致的层间漂移。

## 9) 图谱质量增强（本版新增）

- 关系抽取支持谓词归一化（如 `depends_on / uses / likes / name_is / role_is`）
- 支持键值型语义抽取（如 `姓名: 张三`、`喜欢: Python, OpenClaw`）
- 关系文件按 `(subject,predicate,object)` 聚合，维护 `support_count / avg_confidence / record_ids / source_paths`
- 新增 `relation_decisions.jsonl`：按 `(subject,predicate)` 做主值裁决，输出 `primary_object / alternatives / resolution_confidence`
- 低价值关系（`cooccurs_with`）只在高质量实体不足时保底，且占比过高会触发自修复重算

## 10) 兼容输入

默认支持：

- 文本：`.txt/.md/.log/.yaml/.yml`
- 结构化：`.json/.jsonl/.csv`
- 数据库：`.db/.sqlite/.sqlite3`

备注：

- YAML 按纯文本读取，不依赖第三方包
- `.abstract.md` / `.overview.md` 作为索引文件自动跳过

## 11) Progressive Disclosure 检索协议（P0 新增）

本 skill 现在把原有 `L0/L1/L2` 明确成一个固定协议，而不是仅仅“存了三层文件”：

1. 先看 `profile_snapshot` / `preferences_snapshot`
2. 再看 `retrieval-hints.json`
3. 再看 `l1_overview.jsonl`
4. 最后才打开 `l2_records.jsonl`

这样做的目标是：

- 当前态问题优先命中 snapshot，减少无谓展开
- bucket 路由问题优先停在 hints / L1
- 只有需要证据、原文或细粒度追溯时才打开 L2

检索协议的机器可读描述保存在：

- `layers/retrieval_protocol.json`
