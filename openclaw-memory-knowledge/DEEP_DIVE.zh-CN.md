# OpenClaw Memory/Knowledge Skill 深度文档（万字版）

## 0. 文档定位与适用范围

本文是 `openclaw-memory-knowledge` skill 的完整技术说明与使用手册，目标是回答三个问题：

1. 这个 skill 到底解决了什么问题，边界在哪里。
2. 它如何在原生 OpenClaw 环境里做到“存量重构 + 后续增量入库 + 自修复”。
3. 结合过去多轮修复记录，当前版本的能力上限、工程风险、可继续优化点分别是什么。

适用对象：

- 需要把历史散乱的 `memory/knowledge` 统一迁移到结构化分层存储的团队。
- 需要低门槛分发（单 hook 包）的 OpenClaw 使用者。
- 需要可审计、可追踪、可持续演进的记忆工程实践者。

不适用对象：

- 期待“纯 LLM 深推理自动建图谱”的场景（本项目目前是规则工程为主，LLM 非必需）。
- 只需要极简本地笔记同步，不需要分层、版本、探针、自修复的人群。

---

## 1. 背景：问题不是“能不能存”，而是“能不能长期可用”

很多 OpenClaw 用户已经有以下事实：

- `memory/` 和 `knowledge/` 里有大量历史内容，格式混杂（`.md/.json/.yaml/.csv/.sqlite`）。
- 数据在增长，但没有统一入库规范，检索命中率波动大，重复和冲突不可控。
- 每次重构都像一次性项目，没有稳定的“后续持续治理”链路。

因此核心问题不是“再造一个目录”，而是建立一个闭环：

- 存量数据一次重构入统一架构。
- 增量数据持续按同一标准入库。
- 质量退化时有探针和修复动作，而不是靠人工排查。
- 整个过程可审计、可解释、可回滚。

这也是本 skill 的核心定位：

- 用 AdaMem 风格分类学做“记忆分桶”。
- 用 OpenViking 风格层次存储做“工程落地”。
- 用 probe -> verify -> repair 做“质量闭环”。

---

## 2. 核心目标与当前结论

### 2.1 两个核心目标

目标 A：别人拿到这个 skill（或 hook 包）后，能够深度读取并分析自己的历史 `memory/knowledge`，完成结构化重构。  
目标 B：后续新增或变更的数据能够自动按同一架构增量入库，且可持续治理。

### 2.2 当前版本结论

基于当前脚本实现（`bootstrap_restructure.py` + `incremental_ingest.py` + `self_evolve.py` + `auto_migrate.py` + hook handler）可以直接实现上述两个目标。

这不是“概念可行”，而是工程链路已经闭环：

- 首次：bootstrap 全量重构。
- 后续：ingest 增量入库（文件变化检测 + UPDATE 语义 + 检索增强去重）。
- 每次 apply 后：按“有无实质变更”决定是否运行 self_evolve 修复。

---

## 3. 架构总览

### 3.1 分层结构

目标目录（默认落在 `<workspace>/memory/.adaptr-v1`）：

- `viking/...`：业务数据层（按 bucket 落盘）
- `layers/l0_abstract.json`：全局摘要
- `layers/l1_overview.jsonl`：bucket 摘要
- `layers/l2_records.jsonl`：原子记录层
- `layers/retrieval-hints.json`：检索提示层
- `state/record_hashes.txt`：内容哈希状态
- `state/processed_files.json`：文件指纹状态
- `reports/*.json`：执行报告
- `reports/rejected_*.jsonl`：门控拒绝审计
- `viking/archive/records.jsonl`：TTL 归档

### 3.2 8 桶分类

Memory 侧：

- `memory.profile`
- `memory.preferences`
- `memory.events`
- `memory.agent_skill`
- `memory.working`

Knowledge 侧：

- `knowledge.facts`
- `knowledge.procedures`
- `knowledge.references`

### 3.3 分类决策链

`classify_bucket()` 采用四级优先链：

1. P1 路径规则优先（高置信）
2. P2 frontmatter / `Category:` 显式标签
3. P2.5 knowledge 文件名/路径专用策略
4. P3 关键词计分兜底（动态置信度计算）

同时通过 `infer_effective_source_kind()` 做 source_kind 二次校正，减少“路径叫 notes 就全当 memory”这类误分。

---

## 4. 数据模型设计

### 4.1 L2 记录关键字段

每条原子记录除了文本本体，还维护：

- `id`
- `content_hash`
- `source_kind`
- `source_path`
- `locator`
- `bucket`
- `confidence`
- `entities`
- `relations`
- `tags`
- `version`
- `ttl`
- `heat`
- `last_accessed`
- `memory_function`
- `formation_mode`
- `trust_tier`
- `memory_policy`
- `metadata`（含 section_title、source_kind 推断信息）

这组字段覆盖了“来源追溯、版本演进、热度治理、检索提示、图谱关系、质量门控”六个维度。

### 4.2 UPDATE 语义

增量不是 append，而是“文件级替换 + 记录级版本递增”：

- 按变更文件先移除旧 L2 记录。
- 重抽取新记录。
- `(source_path, locator)` 作为版本键，`version` 自动递增。
- 重建 bucket/L0/L1/L2/entities/relations/hash-state 物化视图。

这解决了历史上“同一条 profile 改了 3 次，库里堆 3 份互相冲突文本”的问题。

### 4.3 P0 元数据增强：function / formation / trust / policy

这一轮 P0 升级，没有推翻现有 8 桶，而是补了 4 个治理维度：

- `memory_function`
  - `factual`
  - `experiential`
  - `working`
- `formation_mode`
  - `bootstrap`
  - `ingest`
  - `runtime`
  - `manual`
- `trust_tier`
  - `curated`
  - `extracted`
  - `generated`
- `memory_policy`
  - `persist`
  - `private`
  - `ephemeral`

它们的意义分别是：

- `memory_function`：这条记录在认知上承担什么角色
- `formation_mode`：这条记录是怎么形成的
- `trust_tier`：这条记录可信度来自人工整理还是规则抽取
- `memory_policy`：这条记录是否应该进入长期层

这样做的价值是：不改变原有 bucket 语义，但为后续 TTL、检索、归档、冲突裁决、prompt 注入提供了更细的控制面。

### 4.4 Current-State Snapshot：从 collection 派生出 profile / preferences 快照

LangMem 给这个项目最直接的启发，是把“当前有效状态”和“可累积历史片段”分开。

当前版本已经新增两个派生文件：

- `layers/profile_snapshot.json`
- `layers/preferences_snapshot.json`

它们不是替代原始 L2，而是从以下来源中抽出“当前态”：

- `memory.profile`
- `memory.preferences`
- frontmatter 字段
- `key: value` / `key：value` 行
- 已抽取出的高价值关系

裁决顺序优先考虑：

1. `trust_tier`
2. `version`
3. `last_accessed`
4. `confidence`

这样，Agent 回答“当前身份/地区/偏好/常用工具是什么”时，不再需要每次从一堆碎片记录里临时汇总。

---

## 5. 读取与抽取：从“文本切片”升级到“结构感知”

### 5.1 Markdown 结构切块

当前实现支持 Markdown section 感知切块（`## / ###` 边界），并把 `section_title` 带入 metadata。  
相比按空行切块，优势是：

- 记录不会丢失“属于哪个章节/日期事件”的上下文。
- 检索命中后可回溯章节语义。

### 5.2 flash 记忆

出现 `⚡` 的内容会打 `flash` 标签，并进入 `retrieval-hints.flash_records`，实现“高优先级记忆标注”。

---

## 6. 图谱链路：从弱共现走向可裁决关系

### 6.1 关系来源

关系抽取来自三条路径：

1. 规则谓词句式（中英文）
2. 键值语义（`key: value`）
3. Markdown 表格关系抽取

不足语义时才降级到低价值 `cooccurs_with` 保底。

### 6.2 谓词标准化

`PREDICATE_ALIASES` 把中英表达统一到标准谓词，如：

- `是/is/are -> is_a`
- `喜欢/likes -> likes`
- `依赖/depends on -> depends_on`
- `is responsible for -> owns`

减少“同义不同写法”导致的图谱碎片。

### 6.3 聚合与冲突裁决

关系写入不是简单逐条落盘，而是聚合后产出：

- `support_count`
- `avg_confidence`
- `record_ids`
- `source_paths`
- `latest_version`

并生成 `relation_decisions.jsonl` 做“同一 `(subject,predicate)` 的主值裁决”，保证可解释的 primary object。

---

## 7. 增量去重：从文本相似升级到原生检索分数

### 7.1 当前去重策略

`incremental_ingest.py` 已接入 `native_memory_search.py`，底层调用：

- `openclaw memory search --query ... --json`

去重判定直接使用检索结果 `score`（0~1）。

关键点：

- 主判定不再依赖 `SequenceMatcher`。
- 默认按 bucket 使用差异化阈值：
  - memory 桶更严格（0.94）
  - knowledge 桶更宽（例如 facts 0.84）
- 记录 `dedup_audit_*.jsonl`，支持审计每次“判重跳过”的依据。

### 7.2 distance 防御策略

当后端返回 `distance` 而非 `score` 时，不做通用 `1 - dist` 盲转换。  
原因是不同向量库 distance 含义差异很大（cosine/L2/IP）。当前仅保留保守特例：`distance==0 -> score=1.0`。

### 7.3 自命中过滤

判重时过滤同 `source_path` 候选，避免“新记录检索到自己文件内旧片段”导致误删。

---

## 8. 质量门控与垃圾拦截

### 8.1 写入前门控

`confidence_gate()` 拒绝以下记录：

- 置信度低于阈值
- 文本过短
- 纯符号/数字

### 8.2 重建门控

`rebuild_materialized_views_from_l2()` 也会对既有 L2 行再过一次门控，清理历史遗留低质量记录，避免“修复流程把脏数据原样写回去”。

### 8.3 Memory Policy：什么不该进入长期记忆

当前版本新增了明确的长期记忆策略：

- `memory_policy: private`
- `memory_policy: ephemeral`
- `memory_policy: persist`
- `<private>...</private>`

规则很直接：

- `private`：不进入 L2，不进入 graph，不进入 retrieval layers
- `ephemeral`：允许抽取，但会被重路由到 `memory.working`
- `persist`：按正常链路入库

这里最关键的工程细节是：policy 不只在首次抽取时生效，也会在“以 L2 为准重建物化视图”时继续生效。这样即使历史 L2 里混入了不该长期保留的数据，后续自修复也能把它重新清掉。

### 8.4 拒绝审计

被拒绝记录落盘到 `reports/rejected_*.jsonl`，可回看具体原因与文本预览。

---

## 9. TTL / 热度 / 归档治理

### 9.1 入库初始策略

按路径与 bucket 自动推断：

- `ttl`（`7d/90d/permanent`）
- `heat`（`hot/warm/cold`）
- `last_accessed`

### 9.2 自修复策略

`self_evolve` 的 TTL probe 会检测：

- 超 TTL：降级 `heat -> cold`
- 超 2xTTL：迁移到 `viking/archive/records.jsonl`

修复动作会写入 action 日志，归档也做去重合并。

---

## 10. 自进化链路：Probe -> Verify -> Repair

### 10.1 Probe 范围

当前包含至少 8 类检查：

1. L0 一致性
2. L1 覆盖率
3. bucket 与 L2 计数一致性
4. L2 去重状态
5. 图谱是否有边
6. 图谱弱关系占比
7. 关系裁决一致性
8. hash-state 覆盖
9. memory+knowledge 覆盖
10. TTL/热度卫生

### 10.2 Repair 机制

核心原则：尽量以 L2 为源重建物化视图，避免“补一个文件、坏另一层”。

可触发动作包括：

- L2 去重
- TTL 修复与归档
- 图谱字段重算
- 关系裁决重建
- 全量物化视图重建

当发现 coverage 问题时，明确返回 `manual_action_required`，不伪装成自动修复成功。

### 10.3 跨轮修复提示

修复后仍有失败项时，报告里有 `after_repair_note`，明确提醒“可能需要再跑一轮 repair 或人工处理”。

---

## 11. 零配置运行链路

### 11.1 自动入口

`python scripts/auto_migrate.py`

能力：

- 自动发现 OpenClaw workspace
- 自动选择 `bootstrap` 或 `ingest`
- 可选 quiet/summary 输出
- 有锁（`.auto_migrate.lock`）避免并发重入
- 根据“是否有实质变更”决定是否触发 self_evolve

### 11.2 Hook 触发

`hooks/openclaw/handler.js` 在 `agent:bootstrap` 自动执行：

- 过滤 subagent 会话
- 自动定位 workspace
- 同步内置 skill payload 到 `~/.openclaw/skills/openclaw-memory-knowledge`
- 调用 `auto_migrate.py --quiet --emit-summary-json`
- 将摘要注入 bootstrap 状态文件

### 11.3 分发方式

推荐单包 hook 分发：

- 打包：`bash scripts/package_hook.sh`
- 产物：`dist/memory-knowledge-auto-migrate-hook-*.zip`

兼容安装路径：

- 若环境支持：`openclaw hooks install <zip>`
- 若不支持 install 子命令：手工解压到 `~/.openclaw/hooks/` 并 enable

---

## 12. 运行手册（实操版）

### 12.1 首次迁移（推荐）

```bash
python scripts/auto_migrate.py --mode bootstrap
```

### 12.2 后续增量

```bash
python scripts/auto_migrate.py --mode ingest
```

### 12.3 仅预览不落盘

```bash
python scripts/auto_migrate.py --no-apply
```

### 12.4 手动自修复

```bash
python scripts/self_evolve.py --workspace-root /path/to/workspace --repair
```

### 12.5 增量去重调参示例

```bash
python scripts/incremental_ingest.py \
  --workspace-root /path/to/workspace \
  --apply \
  --retrieval-dedup on \
  --retrieval-threshold 0.94 \
  --retrieval-search-min-score 0.25 \
  --retrieval-limit 8 \
  --retrieval-max-calls 80
```

---

## 13. 报告与审计解读

### 13.1 重点报告文件

- `reports/bootstrap_*.json`
- `reports/ingest_*.json`
- `reports/self_evolve_*.json`
- `reports/dedup_audit_*.jsonl`
- `reports/rejected_*.jsonl`

### 13.2 ingest 报告关键字段

- `changed_files`
- `old_records_removed`
- `new_records_inserted`
- `duplicate_by_hash`
- `duplicate_by_semantic`（兼容名）/ retrieval 对应字段
- `version_bumped_records`
- `dedup_audit_count`

### 13.3 self_evolve 报告关键字段

- `health_score`
- `fail_count`
- `actions`
- `after_repair`
- `after_repair_note`

---

## 14. 修复演进纪要（按问题类型）

本节基于多轮真实评审与修复，聚焦“为什么改、改了什么、效果如何”。

### 14.1 分类误判问题

初始问题：关键词分类过强，agent skill/knowledge 容易错桶。  
修复：

- 增加 frontmatter/`Category:` 优先。
- 新增 `memory.agent_skill` 桶。
- 增加 knowledge 专用路径文件名策略。
- 增加 source_kind 二次推断和路径 token 匹配收敛。

效果：结构化 memory 命中明显提升，knowledge 误入 memory 的概率降低。

### 14.2 Markdown 上下文丢失

初始问题：按空行切片导致语义断裂。  
修复：按 `##/###` 切块 + `section_title` 入 metadata + flash 标记。  
效果：检索可追溯到章节语义，事件型记录质量提升。

### 14.3 UPDATE 语义缺失

初始问题：变更文件反复 append，历史冲突累积。  
修复：文件级替换 + `(source_path, locator)` 版本递增 + 全量物化重建。  
效果：重复堆积显著下降，版本语义可追踪。

### 14.4 TTL 只有检查没有闭环

初始问题：TTL 只探测不处理。  
修复：`enforce_ttl_policy()` 落地降级与归档动作，归档写入 `viking/archive/records.jsonl`。  
效果：冷数据治理从“告警”变成“执行”。

### 14.5 self_evolve 修复路径绕过门控

初始问题：修复重建时可能把低质量历史数据带回。  
修复：重建入口复用 L2 门控并输出 rejected_rebuild 报告。  
效果：历史脏数据可在修复链路中持续净化。

### 14.6 关系图语义噪音高

初始问题：大量 `cooccurs_with`，关系价值低。  
修复：

- 谓词归一化扩展
- KV 与表格关系抽取增强
- 噪音实体过滤
- 弱关系占比探针 + 自动重算
- 关系冲突裁决落盘

效果：图谱从“能连边”提升到“可裁决、可解释”。

### 14.7 语义去重名不副实

初始问题：历史阶段存在“文本相似去重”倾向。  
修复：接入 OpenClaw 原生 memory search，直接使用返回 `score` 判重，SequenceMatcher 退出主判定链。  
效果：去重判定与检索语义空间一致，且可审计。

### 14.8 安装与分发门槛

初始问题：用户需要手配路径或多步安装。  
修复：

- 单 hook 自包含包（含 scripts + skill payload）
- handler 自动同步 skill 到 `~/.openclaw/skills`
- 文档提供 install 与兼容 fallback

效果：可“单包安装，自动触发”，且兼容老版本。

### 14.9 失败可观测性不足

初始问题：失败时不清楚原因。  
修复：`auto_migrate.py` 增加结构化 summary、error_detail、hints、emit-summary-json。  
效果：hook/UI 可直接展示可执行排障信息。

### 14.10 破坏性重构风险

初始问题：bootstrap 中断可能留下半成品状态。  
修复：`backup-mode` + 失败回滚机制。  
效果：重建风险可控，故障恢复路径明确。

---

## 15. 与原生 OpenClaw 能力的关系

### 15.1 不是替代，而是编排增强

原生 OpenClaw 已具备 builtin/hybrid 检索能力；本 skill 的作用是把“分散内容”治理成“可持续索引资产”，并把质量控制工程化。

可以理解为：

- OpenClaw 原生能力：高质量检索引擎。
- 本 skill：入库、分层、治理、修复与可审计流水线。

### 15.2 优势

- 可分发、低人工干预。
- 存量与增量统一架构。
- 有版本、TTL、热度、探针、修复、审计。

### 15.3 不足

- 关系抽取仍以规则为主，开放语义覆盖有限。
- knowledge 自由文本分类仍有误判上限。
- 去重依赖检索返回 score，无法直接做底层向量算子控制。

---

## 16. 设计边界与剩余挑战

### 16.1 关系抽取上限

当前规则 + 表格 + KV 能覆盖高频模式，但长尾谓词仍会漏。  
方向：引入可插拔轻量关系解析器（非强依赖 LLM）。

### 16.2 置信度语义仍偏启发式

`confidence_from_keyword_scores` 已优于硬编码，但本质仍是启发式，不是统计学习输出。  
方向：引入离线标注评估集，做参数标定与回归测试。

### 16.3 knowledge 文本分桶难度

自由文本知识库语义跨度大，分类天然难于结构化 memory。  
方向：为 knowledge 增加更强的“文件名+结构特征+检索反证”联合策略。

### 16.4 自修复多轮稳定性

当前有跨轮提示，但仍依赖“再跑一轮”处理某些副作用失败。  
方向：引入 repair plan DAG，把可串行修复链编码成显式阶段。

---

## 17. 参数调优建议

### 17.1 对 memory-heavy 场景

- `retrieval-threshold` 保持高（0.94）
- `retrieval-search-min-score` 可略升（0.2~0.3）
- `retrieval-max-calls` 中等（50~80）

### 17.2 对 knowledge-heavy 场景

- 重点关注分桶阈值（facts/procedures/references）
- 提高 `dedup-audit-max-records` 便于观察误判
- 必要时降低 global threshold，但优先改 bucket threshold

### 17.3 大规模仓库

- 先 `--no-apply` 观察扫描规模
- 控制 `--max-file-size-mb` 与 `--max-records-per-file`
- 使用 `--include-hidden` 前先确认隐藏目录价值

---

## 18. 运维与故障处理

### 18.1 常见故障

1. `workspace_not_detected`：事件上下文缺少 workspacePath。  
2. `openclaw_not_found`：环境 PATH 无 OpenClaw CLI。  
3. 写权限失败：目标目录不可写。  
4. 修复后仍失败：需要再跑一轮 repair 或人工动作。

### 18.2 最小排障顺序

1. 看 hook 注入摘要（error/error_detail/hints）。
2. 打开最新 `reports/*.json`。
3. 核对 `target_root` 是否正确。
4. 手动执行 `auto_migrate.py` 复现。
5. 手动执行 `self_evolve.py --repair`。

---

## 19. 与过去版本相比的关键提升

可以把演进概括为四条主线：

1. 从“可跑”到“可分发”：单 hook 包 + 自动同步 skill。  
2. 从“可入库”到“可治理”：TTL、热度、归档、探针、修复。  
3. 从“规则堆叠”到“可审计”：dedup_audit、rejected_report、summary+hints。  
4. 从“文本匹配去重”到“检索分数判重”：与原生 hybrid 引擎对齐。

---

## 20. 推荐落地策略（团队视角）

### 20.1 第一阶段（上线）

- 只启用自动 bootstrap + ingest + 自修复。
- 每天审阅一次报告样本（dedup_audit/rejected/self_evolve）。

### 20.2 第二阶段（稳定）

- 固化 bucket 阈值策略。
- 针对你们语料补充 `PREDICATE_ALIASES` 与字段映射。
- 建立“失败探针 -> owner”值班规则。

### 20.3 第三阶段（优化）

- 引入离线评测集（分类准确率、去重误杀率、图谱有效边占比）。
- 把回归测试接入 CI，防止后续迭代回退。

---

## 21. 快速问答（FAQ）

### Q1：必须额外部署 embedding 服务吗？

不需要。当前实现依赖 OpenClaw CLI 的原生 memory search，非外部 localhost embedding HTTP 服务。

### Q2：只给用户一个 zip 能自动跑吗？

可以。推荐分发 hook 自包含 zip。支持 install 子命令时可一步安装；不支持时有手工兼容路径。

### Q3：会不会破坏原有数据？

`bootstrap --mode rebuild --apply` 会重建目标目录，但已经有备份+失败回滚机制，风险可控。

### Q4：为什么 self_evolve 有时“修复后仍失败”？

因为某些修复会触发新的探针失败，当前策略是显式报告并建议二次 repair，不做静默掩盖。

### Q5：这个架构是不是已经“完美”？

不是。它是工程上可用、可分发、可治理的版本；在开放语义关系抽取和 knowledge 高自由文本分类方面仍有可见上限。

---

## 22. 最终结论

如果你的目标是：

1. 让别人把已有 `memory/knowledge` 自动重构到统一架构；
2. 让后续新增/变更数据持续按同一架构入库并可治理；

那么当前 `openclaw-memory-knowledge` 已经可以直接承担生产级试运行，并具备以下必要条件：

- 自动化链路完整（bootstrap -> ingest -> evolve）
- 分发链路完整（单 hook 包）
- 质量链路完整（门控 + 探针 + 修复 + 审计）
- 风险控制完整（锁、备份、回滚、失败提示）

它不是“最终态”，但已经跨过“演示代码”阶段，进入“可运营工程系统”阶段。

---

## 23. 附：常用命令索引

```bash
# 1) 推荐：零配置自动模式
python scripts/auto_migrate.py

# 2) 首次全量重构
python scripts/auto_migrate.py --mode bootstrap

# 3) 后续增量
python scripts/auto_migrate.py --mode ingest

# 4) 仅预览
python scripts/auto_migrate.py --no-apply

# 5) 手动自修复
python scripts/self_evolve.py --workspace-root /path/to/workspace --repair

# 6) 打包 hook（推荐分发形式）
bash scripts/package_hook.sh

# 7) 打包 skill（仅在需要单独 skill 包时）
bash scripts/package_skill.sh
```

---

## 24. 附：建议你下一步优先做的三件事

1. 建一个最小评测集（memory 100 条 + knowledge 100 条），固定评估分类、去重、图谱三项指标。  
2. 把 `reports/dedup_audit_*.jsonl` 和 `self_evolve` 结果接入你们内部日报，做到持续可观测。  
3. 对你们高频业务词补充谓词映射和字段映射，降低关系图谱长尾噪声。
