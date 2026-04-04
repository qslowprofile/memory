# OpenClaw Memory & Knowledge 架构重构工具使用指南
## 写在前面：你有这个问题吗？

用 OpenClaw 一段时间后，工作区通常会长这样：

```
memory/
  2026-03-01.md    ← 流水账日记
  2026-03-02.md
  2026-03-10.md
  user/
    memories/      ← 手动建的，结构因人而异
  ...（越来越乱）
MEMORY.md          ← 越来越大，开始截断
```

问题随之而来：

- `memory_search` 召回率下降——同一件事散落在三个文件里，每次只找到一条
    
- `MEMORY.md` 撑破上限，新写进去的内容反而被截断读不到
    
- 日记文件堆到几十个，agent 每次加载慢、理解也慢
    
- knowledge 目录没有统一结构，知识之间缺乏关联，检索不准
    
- 换新 session 时，上下文重建靠运气
    

这些问题的根源不是"数据没有备份"，而是 **memory 和 knowledge 本身缺乏结构**。内容散、分类乱、关系缺，是记忆系统退化的真正原因。

这个工具就是为了解决这些问题而生的。

---

## 这是什么

一个 **memory/knowledge 深度重构引擎**，以 OpenClaw Hook + Skill 的形式运行。

**它的核心是重构，不是迁移。**

所谓"重构"，是指把你现有的 memory/knowledge 原始内容，按照统一的分层架构重新组织：

- 按内容类型分桶（人物信息、行为偏好、事件、技能、知识等 8 个桶）
    
- 建立 L0/L1/L2 三层索引，支持从摘要到原子记录的快速定位
    
- 抽取实体和关系，构建可查询的知识图谱
    
- 后续增量更新采用"版本替换"语义，不再无限堆积
    
- 定期自修复：去重、一致性检查、TTL 老化、图谱质量维护
    

整个过程**零配置，自动运行**：装好 hook 后，每次开新会话自动触发，首次做全量重构，后续只处理变更文件。

---

## 先看这里：我适合用这个吗？

|你的情况|建议|
|---|---|
|刚开始用 OpenClaw，memory 很少|可以装，会建立好的基础结构|
|用了一段时间，memory/knowledge 越来越乱|✅ 强烈推荐，正是目标场景|
|有大量手写知识库（`knowledge/`）|✅ 推荐，知识库也会被重构并建立图谱|
|担心破坏现有数据|放心，首次重构会自动备份旧数据到同级目录|
|想手动控制重构时机|支持，见"手动模式"一节|
|不想装 hook，只想单次重构|支持，见"手动模式"一节|

---

## 一、安装（一次性操作）

### 步骤

1. 在大象里把 `memory-knowledge-auto-migrate-hook.zip` 发给 OpenClaw
    
2. 说："帮我安装这个 hook"
    

OpenClaw 会自动读取包内的 `INSTALL.md` 并执行安装，整个过程 30 秒内完成。

### 安装后验证

开一个**新会话**，等 1~3 分钟。v6 起，hook 运行结束后会在工作区生成持久化状态文件，**无论客户端是否支持 bootstrap 注入均可查看**。

**方法一（推荐）：看落盘状态文件** — 路径以 `last_status.json` 的 `summary.target_root` 为准（可能是 `memory/.adaptr-v1` 或 `workspace/.adaptr-v1`，取决于目录结构）。直接问 OpenClaw：「帮我找 `.adaptr-v1/state/last_status.json`，读出 `summary.target_root` 和 `summary.status`」

`summary.status == "ok"` 表示成功；`summary.status == "warning"` 表示有问题，继续看 `last_status.md` 的详情。

**方法二：bootstrap 注入文件（若客户端支持）** — 若客户端支持 bootstrap 注入，新会话中可在上下文看到 `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`，看到 `Memory Migration Done` 即正常。⚠️ 这是虚拟文件，磁盘上不存在，全盘搜索找不到属正常现象。方法一始终可用。

### 出现 `Memory Migration Warning`？

不要慌，这通常是安全跳过，不是出错。查看 `last_status.json` 的 `summary.status` 字段确认，再看 `last_status.md` 里的 `Error code / Error detail / Hints`，按提示操作即可。最常见的原因是 bootstrap 事件没有携带可信工作区路径（安全跳过，不是出错）。

---

## 二、重构后的架构全景
<img width="4518" height="522" alt="adaptr_architecture" src="https://github.com/user-attachments/assets/a6e36c8e-4b94-4d23-9780-e16a8cc08f6c" />

安装后，你的 `memory/` 下会多出一个隐藏目录 `.adaptr-v1/`，这是重构后的数据落地处：

```
memory/.adaptr-v1/
├── viking/
│   ├── user/
│   │   ├── memories/
│   │   │   ├── profile.jsonl        ← 人物信息、UID、MIS
│   │   │   ├── preferences.jsonl    ← 行为偏好
│   │   │   ├── events.jsonl         ← 事件记录
│   │   │   ├── entities.jsonl       ← 实体图谱（人/物/概念）
│   │   │   ├── relations.jsonl      ← 关系图谱
│   │   │   └── relation_decisions.jsonl  ← 关系主值裁决
│   │   └── knowledge/
│   │       ├── facts.jsonl          ← 事实性知识
│   │       ├── procedures.jsonl     ← 操作步骤/流程
│   │       └── references.jsonl     ← 参考资料
│   ├── agent/
│   │   └── skills/buffer.jsonl      ← Agent 技能/工具记录
│   ├── session/
│   │   └── working/buffer.jsonl     ← 当前工作记录
│   └── archive/
│       └── records.jsonl            ← 已归档（过期）内容
├── layers/
│   ├── l0_abstract.json             ← 全局统计摘要（入口）
│   ├── l1_overview.jsonl            ← 分桶摘要
│   ├── l2_records.jsonl             ← 全量原子记录
│   ├── retrieval-hints.json         ← 检索热点提示
│   ├── profile_snapshot.json        ← 当前态用户画像快照（v5 新增）
│   ├── preferences_snapshot.json    ← 当前态偏好快照（v5 新增）
│   └── retrieval_protocol.json      ← 检索协议描述（v5 新增）
├── state/
│   ├── record_hashes.txt            ← 增量去重指纹
│   └── processed_files.json         ← 已处理文件状态
└── reports/
    ├── bootstrap_*.json             ← 重构报告
    ├── ingest_*.json                ← 增量报告
    ├── rejected_*.jsonl             ← 写入质量门控拒绝记录（v5 新增）
    ├── policy_skipped_*.jsonl       ← memory_policy 拦截审计（v5 新增）
    ├── dedup_audit_*.jsonl          ← 去重裁决审计日志
    └── self_evolve_*.json           ← 自修复报告
```

**原有的** `**memory/*.md**` **文件完全不动**，`.adaptr-v1/` 是新增的结构化层。

---

## 技术背景：站在哪些巨人的肩膀上

`openclaw-memory-knowledge` 的架构设计并非凭空而来，而是在吸收近期 LLM Agent 记忆系统研究成果的基础上，针对 OpenClaw 的实际约束（无向量服务、纯本地文件、个人工作区规模）做出的工程化裁剪与融合。在这里介绍三个核心理论来源，以及本工具在其基础上做出的取舍与延伸。

---

### AdaMem：用户中心的自适应分层记忆

**来源**：AdaMem: Adaptive User-Centric Memory for Long-Horizon Dialogue Agents（[https://arxiv.org/abs/2603.16496](https://arxiv.org/abs/2603.16496), 2026）

AdaMem 提出，单一的语义相似度检索无法满足所有问题类型——有些问题需要人物画像（persona），有些需要近期上下文（working），有些需要跨时间的事件链（episodic），有些需要关系网络（graph）。AdaMem 的核心贡献是把这四类记忆解耦为独立存储，在推理时根据问题类型动态拼接检索路径：先定位参与者，再按问题条件选择语义检索 + 关系图谱扩展的不同组合，最后走专门的角色流水线综合答案。

**本工具借鉴的核心思想**：

- **四类记忆分离**的思路直接映射到 8 桶分类体系：`memory.profile`（persona）、`memory.working`（working）、`memory.events`（episodic）+ 关系图谱（graph）各司其职，而非平铺在一个向量空间里混检
    
- **用户中心**的原则体现在 `viking/user/` 目录与 `viking/agent/` 目录的分离——用户的个人知识、偏好、人物关系与 Agent 自身的技能、工具配置严格区分，避免上下文污染
    
- **静态粒度不适配问题**的洞见启发了分桶差异化 TTL 策略：`memory.profile` 是 `permanent`，`memory.working` 是 `7d`，`memory.events` 是 `90d`，不同类型记忆以不同生命周期老化
    

**本工具做的裁剪**：AdaMem 的推理时路由依赖 LLM 判断，本工具在 bootstrap 阶段就完成分类，无需每次检索时再做 LLM 路由——用写入时的计算换取检索时的零开销。

---

### OpenViking：文件系统范式的 Agent 上下文数据库

**来源**：volcengine/OpenViking（[https://github.com/volcengine/OpenViking](https://github.com/volcengine/OpenViking)，火山引擎开源，2026）

OpenViking 是字节/火山引擎为 OpenClaw 等 AI Agent 设计的开源上下文数据库，最核心的贡献是两点：

1. **文件系统范式**取代平面向量存储：记忆、资源、技能统一用 `viking://` URI 管理，目录层次本身就是一种结构化索引，不需要额外的元数据数据库
    
2. **L0/L1/L2 三层按需加载**：L0 ~100 tokens 的全局摘要、L1 ~2k tokens 的桶级摘要、L2 完整原子记录，检索时从 L0 入口逐层下钻，平均只需加载 ~550 tokens（对比传统向量检索一次性加载全部 10k+ tokens，节省约 95%）
    

**本工具借鉴的核心思想**：

- **三层索引结构**（`l0_abstract.json` → `l1_overview.jsonl` → `l2_records.jsonl`）完整复用了 OpenViking 的分层范式，L0 是全局统计入口，L1 是每桶摘要，L2 是可按需精读的原子记录
    
- **目录即索引**的哲学体现在 `viking/user/memories/`、`viking/user/knowledge/`、`viking/agent/skills/` 等路径设计上——文件路径本身就携带了一级分类信息，降低了 Agent 的认知负担
    
- **自迭代循环**（self-evolving）的概念对应了 `self_evolve.py` 的 probe → verify → repair 闭环
    

**本工具做的裁剪**：原版 OpenViking 需要 embedding 模型（VLM + embedding provider）和 Go/Rust 工具链，复杂度较高。本工具将其裁剪为**纯 Python 标准库实现**，检索增强去重直接复用 OpenClaw 原生 `memory search`，无需任何外部 HTTP 服务——用检索精度上的少量妥协换取零依赖的可部署性。

---

### A-MEM：Zettelkasten 启发的知识网络与记忆演化

**来源**：A-MEM: Agentic Memory for LLM Agents（[https://arxiv.org/abs/2502.12110](https://arxiv.org/abs/2502.12110)）

A-MEM 把 Zettelkasten 笔记法引入 Agent 记忆系统：每条新记忆写入时，不只是存原文，还要生成包含关键词、标签、上下文描述的结构化 note，并与历史记忆建立显式链接；当新记忆加入时，还会触发对相关旧记忆的属性更新，让记忆网络持续自我精炼。这使得 Agent 能在回答问题时沿记忆之间的链接游走，而不是孤立地做向量近邻搜索。

**本工具借鉴的核心思想**：

- **结构化 note 写入**的思想体现在每条 L2 记录的元数据字段上：`tags`、`entities`、`section_title`、`heat`、`ttl`，写入时就附带结构，不只存原始文本
    
- **⚡ flash 标注机制**是 A-MEM "关键记忆优先链接"原则的轻量实现——以 `⚡` 开头的行自动打 `flash` tag，进入 `retrieval-hints.json` 的快速访问层，确保高价值记忆不被淹没在海量普通记录中
    
- **记忆演化**的概念对应了增量入库的"文件级替换"语义：旧版本被移除，新版本递增 `version` 写入，记录的 `last_accessed` 字段也随访问频率更新，实现了轻量级的记忆热度演化
    

**本工具做的裁剪**：A-MEM 的记忆链接和演化依赖 LLM 推理，成本较高。本工具改用**规则驱动的图谱构建**：谓词归一化、键值型语义抽取、`(subject, predicate)` 主值裁决均为确定性规则，不调用 LLM，在个人工作区规模下保持毫秒级吞吐。

---

### Memory Survey：记忆分类框架与已知缺口

**来源**：Memory in the Age of AI Agents: A Survey（[https://arxiv.org/abs/2512.13564](https://arxiv.org/abs/2512.13564),）

这篇 survey 是迄今最系统的 Agent 记忆综述，提出了 Sensory / Working / Episodic / Semantic / Procedural 五类分类体系，并定义了 **storage × operation × management** 三维框架。其中 management 层涵盖记忆优先级动态调整、跨源冲突解决、主动遗忘三个子问题——这也是当前多数实现的共同缺口。

本工具的 8 桶设计与 survey 框架的对应关系：Working → `memory.working`（7d TTL）；Episodic → `memory.events`；Semantic → `knowledge.facts` + `knowledge.references`；Procedural → `knowledge.procedures` + `memory.agent_skill`；Sensory memory（context window 内即时上下文）不在本工具范畴内。

**本工具做的裁剪**：survey 中 management 层的"主动遗忘"（记忆超阈值时 LLM 合并/压缩旧记忆）和"Episodic 时序检索"（按时间轴游走事件链）目前均未实现，是已知缺口。v5 的 LLM 增强环节填补了部分写入质量门控，但尚未触及跨源冲突解决。

### LangMem 与 Claude-Mem：同方向，不同约束

**来源**：LangMem（[https://github.com/langchain-ai/langmem](https://github.com/langchain-ai/langmem)）；Claude-Mem（[https://github.com/thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)）

LangMem 是工程化最完整的 Agent 长期记忆 SDK，提供可插拔 Storage Backend、Memory Manager、Namespace 隔离，支持跨 session 记忆共享；最核心的两个能力是**冲突解决**（同一事实从不同 source 进来时，LLM 裁决保留哪个或如何合并）和 **cross-thread namespace**（多 agent / 多对话的记忆隔离）。Claude-Mem 则走另一条路线：每轮对话结束后由 LLM 主动提取值得记住的内容写入 `CLAUDE.md`，下次对话开始时注入 context——实时性强，记忆质量高，适合单 agent 场景。

**本工具做的裁剪**：本工具的核心约束是零 pip 依赖、无外部向量服务、纯本地文件、OpenClaw 原生 `memory search` 作为唯一检索入口——这使两个 SDK 都无法直接套用。与 LangMem 的差距在于缺少冲突解决；与 Claude-Mem 的差距在于缺少实时 LLM 提取（v3 之前完全没有 LLM 调用，v5 的 LLM 环节是可选增强而非核心路径）。反过来，本工具擅长 LangMem/Claude-Mem 都不具备的能力：**批量历史文件迁移**——把数百个 diary 文件一次性结构化归档，这也是它最初被设计出来的原因。

### 设计决策总结

|问题|学术方案|本工具方案|取舍原因|
|---|---|---|---|
|记忆分类|LLM 推理路由（AdaMem）|写入时规则分桶|消灭检索时 LLM 开销|
|索引结构|L0/L1/L2 分层（OpenViking）|完整复用|直接适配 OpenClaw 场景|
|去重|向量相似度（OpenViking）|OpenClaw 原生 memory search|零外部 embedding 依赖|
|知识图谱|LLM 关系抽取（A-MEM）|规则驱动 + 谓词归一化|个人工作区规模，规则够用|
|记忆演化|LLM 触发属性更新（A-MEM）|文件级版本替换 + TTL 老化|确定性语义，可审计|
|自修复|无（多数方案缺失）|probe→verify→repair 闭环|OpenViking self-evolving 启发|

这套设计的核心主张是：**在个人工作区规模下，规则驱动的确定性架构比 LLM 驱动的自适应架构更可靠、更可审计、成本更低**。当记忆数量从百万级降到数万级时，精确结构带来的收益远大于 LLM 自适应带来的灵活性。

---

## 三、核心机制：一次读懂

### 3.1 分桶重构（8 个内容桶）

重构的第一步是**内容分类**。原始 memory/knowledge 文件中的内容，会被切块并自动分配到 8 个桶里：

|桶|存什么|典型来源|
|---|---|---|
|`memory.profile`|姓名、UID、MIS、身份|MEMORY.md 里的用户信息段|
|`memory.preferences`|行为偏好、沟通风格|"秋实喜欢简洁回复"这类内容|
|`memory.events`|事件、里程碑、决策|日记文件|
|`memory.agent_skill`|Agent 技能、工具配置|skills/、TOOLS.md|
|`memory.working`|当前任务状态|SESSION-STATE.md、临时工作记录|
|`knowledge.facts`|事实性知识|knowledge/ 下的 facts 文件|
|`knowledge.procedures`|操作流程、SOP|knowledge/ 下的操作文档|
|`knowledge.references`|参考资料、规范|knowledge/ 下的 reference 文件|

**分类是全自动的**，优先级是：路径命名 > frontmatter 标注 > 关键词打分兜底。

### 3.2 三层索引（L0/L1/L2）

重构之后，系统会自动建立三层索引，让 OpenClaw 能在不同粒度快速定位内容：

```
L0  l0_abstract.json     ← 全局统计（总量、热点桶、flash 记录摘要）
     ↓
L1  l1_overview.jsonl    ← 每个桶的摘要（top 标签、代表性文本）
     ↓
L2  l2_records.jsonl     ← 全量原子记录（按需精读）
```

`retrieval-hints.json` 是额外的检索加速层，记录热点 bucket、关键词、⚡ 标注的 flash 记录，让高频检索更快触达。

### 3.3 知识图谱构建

重构过程中，系统会从文本中自动抽取**实体**和**关系**，构建轻量级知识图谱：

- 实体抽取：识别人名、工具名、系统名、概念等
    
- 关系抽取：识别 `使用/管理/属于/喜欢` 等谓词，并支持键值型语义（如 `姓名: 张三`）
    
- 关系归一化：多种表述映射为统一谓词，消除冗余
    
- 主值裁决：同一 `(subject, predicate)` 下的多条关系，自动裁决出"主值"写入 `relation_decisions.jsonl`
    

这使得 OpenClaw 在回答"X 和 Y 是什么关系？""谁用了哪个工具？"这类问题时，能直接走图谱而不是纯文本相似度搜索。

### 3.4 增量版本替换（不重复堆积）

原始的 append 式记忆会无限累积，同一件事会存 3 个版本。这个工具改用**文件级替换**语义：

1. 发现 `memory/2026-03-20.md` 有更新
    
2. 找到它在 L2 里的旧记录，全部移除
    
3. 重新抽取这个文件的新内容
    
4. 以 `(source_path, locator)` 为 key 递增版本号写入
    

**同一内容改了就更新，不会出现多个版本同时存在的情况。**

### 3.5 检索增强去重

增量入库时，新记录写入前会先走 OpenClaw 原生 `memory search` 查一遍：

- 相似度超阈值的内容不再重复写入
    
- 灰区（score 0.55–0.85）记录交由 LLM 裁决 duplicate / distinct，避免误删（v3 新增，不可用时回退规则）
    
- 门控拒绝记录写入 `reports/rejected_*.jsonl`，去重裁决审计写入 `reports/dedup_audit_*.jsonl`，两者独立可审计
    

**不依赖任何外部 embedding 服务，直接复用 OpenClaw 自带的 memory backend。**

调用 `scripts/native_memory_search.py` 后，返回 JSON 中可能包含 `rerank` 字段：

```
{
  "result": [...],
  "rerank": {
    "summary": "已整理的上下文摘要（200字内）",
    "used_indices": [0, 2],
    "filtered_indices": [1, 3],
    "ok": true
  }
}
```

### 3.6 TTL + 热度（自动老化）

每条记录入库时自动打上三个标签：

|字段|含义|示例|
|---|---|---|
|`ttl`|生存周期|`7d / 90d / permanent`|
|`heat`|热度|`hot / warm / cold`|
|`last_accessed`|最近访问时间|`2026-03-20T10:00:00Z`|

`self_evolve --repair` 会检查 TTL：

- 超过 TTL → 降级为 `cold`
    
- 超过 2×TTL → 自动移入 `archive/`，不再占用主索引
    

### 3.7 自修复闭环（probe → verify → repair）

每次有实质变更后，`self_evolve.py` 会自动跑一遍：

- L0/L1/L2 层间一致性检查
    
- 重复记录检测与清理
    
- 关系图谱弱关系占比检测（`cooccurs_with` 太多会触发重算）
    
- TTL/热度卫生检查
    
- 修复策略：以 L2 为准重建所有物化视图
    

---

### 3.8 P0 治理字段与 Memory Policy（v5 新增）

在文件 frontmatter 中声明（`memory_policy: private`），或在正文里用 `<private>...</private>` 标签包裹需要保护的片段，agent 就不会把这段内容写入检索层。

- `persist`：正常入库，长期存储（默认）
    
- `ephemeral`：允许抽取，但强制路由到 `memory.working`（7d TTL，不进长期层）
    
- `private`：不进入 L2 / 图谱 / 检索层，拦截记录写入 `reports/policy_skipped_*.jsonl`
    

|字段|取值示例|含义|
|---|---|---|
|`memory_function`|factual / experiential / working|记忆的功能类型|
|`formation_mode`|bootstrap / ingest / runtime / manual|记录的形成方式|
|`trust_tier`|curated / extracted / generated|可信等级（curated 最高）|
|`memory_policy`|persist / private / ephemeral|持久化策略（见下）|

v5 起每条 L2 记录在原有字段基础上新增 4 个治理元字段，不替代 8 桶分类，仅补充「功能类型 / 形成方式 / 可信等级 / 持久化策略」四个维度：

### 3.9 Progressive Disclosure 检索协议（v5 新增）

**rerank 优先级规则：**调用 `native_memory_search.py` 返回结果后，如果 `rerank.ok == true`，Agent 应**优先使用 rerank.summary 作为上下文**，忽略原始 result 列表——summary 已经过滤不相关片段、合并语义重复，直接可用。只有 `rerank.ok == false` 时才 fallback 到原始结果列表。

检索协议的机器可读描述保存在 `layers/retrieval_protocol.json`，也可参考 `references/retrieval-protocol.md`。

- **Step 1** → `layers/profile_snapshot.json` + `preferences_snapshot.json`：当前态用户画像/偏好快照，优先命中「这个用户是谁」「他喜欢什么」类问题，命中即停止
    
- **Step 2** → `layers/retrieval-hints.json`：热点 bucket、关键词、⚡ flash 记录，用于 bucket 路由决策
    
- **Step 3** → `layers/l1_overview.jsonl`：分桶摘要，确认目标 bucket，bucket 层面答得上来即停止
    
- **Step 4**（按需）→ `layers/l2_records.jsonl`：全量原子记录，需要原文证据 / source_path / 版本核查时才打开
    

v5 起将 L0/L1/L2 整理成显式的「渐进式展开」读取顺序。目标是：让 Agent 先从最轻量的层拿到答案，只在必要时才打开全量 L2 记录（节省约 95% token 加载开销）。

## 四、怎么确认运行状态

### 方法一：看落盘状态文件（推荐，v6 新增）

v6 起，每次 bootstrap 运行结束后，hook 会在工作区生成持久化状态文件，不依赖 bootstrap 注入。路径以 `last_status.json` 的 `summary.target_root` 为准（不是固定路径，取决于是否存在 `memory/` 目录）。

直接问 OpenClaw：「帮我找 `.adaptr-v1/state/last_status.json`，读出 `summary.target_root` 和 `summary.status`」

- `summary.status == "ok"`：本次执行成功
    
- `summary.status == "warning"`：查看 `last_status.md` 获取 Error code / Error detail / Hints
    
- `last_status.md` 是人读版，内容为原始 body；机器判断成功/警告以 `last_status.json` 为准
    

### 方法二：bootstrap 注入文件（若客户端支持）

若客户端支持 bootstrap 注入，新会话中可在上下文看到 `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`，内容类似：

⚠️ 这是**虚拟文件**，磁盘上不存在，全盘搜索找不到属正常现象。无论是否显示，方法一始终可用。

```
## Memory Migration Done

Workspace (event): /root/.openclaw/workspace
Skill sync: ok - skill already synced: ~/.openclaw/skills/openclaw-memory-knowledge
Migration: ok - memory/knowledge migration executed
Run mode: ingest
Meaningful changes: yes
Migrate report: .adaptr-v1/reports/ingest_20260325T050000Z.json
Self-evolve report: .adaptr-v1/reports/self_evolve_20260325T050012Z.json
```

### 方法三：看报告文件（详细分析用）

```
# target_root 以 last_status.json.summary.target_root 为准
# 用 OpenClaw 直接问，或自行计算：
# python3 -c "import json,os; [print(json.load(open(p))['summary']['target_root']) for p in [os.path.expanduser('~/.openclaw/workspace/memory/.adaptr-v1/state/last_status.json'), os.path.expanduser('~/.openclaw/workspace/.adaptr-v1/state/last_status.json')] if os.path.exists(p)]"

# 最新重构/增量报告
ls -lt <target_root>/reports/ | head -5

# 全局统计（总量、热点桶）
cat <target_root>/layers/l0_abstract.json

# 检索热点（flash 记录、热桶关键词）
cat <target_root>/layers/retrieval-hints.json
```

## 五、手动模式（高级用法）

只在不走 hook 自动模式时需要这一节。

### 预览（不落库）

```
cd ~/.openclaw/workspace
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py --no-apply
```

### 强制全量重构

```
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py --mode bootstrap
```

### 强制增量入库

```
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py --mode ingest
```

### 手动指定工作区（自动发现失败时）

```
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py \
  --workspace-root /path/to/openclaw-or-workspace
```

### 只跑自修复

```
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/self_evolve.py \
  --workspace-root /root/.openclaw/workspace \
  --repair
```

### 直接指定源路径（完全脱离自动发现）

```
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/bootstrap_restructure.py \
  --memory-path /path/to/memory \
  --knowledge-path /path/to/knowledge \
  --target-root /path/to/arch_root \
  --apply
```

---

## 六、常见问题Q&A

| Q | A |
|---|---|
| **为什么不用向量数据库？** | 向量数据库（如 Chroma、Milvus）能提供毫秒级的语义相似度检索，是 RAG 系统的标配。我们在设计时也评估了这个方向，最终放弃的原因有三：<br><br>**一、部署复杂性。** 向量数据库需要独立进程运行，并依赖 embedding 模型服务。在 CatClaw 这种轻量个人助手场景，引入这么重的基础设施明显杀鸡用牛刀。<br><br>**二、规模不匹配。** 向量检索的优势在数百万条记录以上的规模。个人工作区的记忆通常在数千条以内，这个规模下，结构化目录+关键词检索的组合完全够用，向量检索的边际收益接近零。<br><br>**三、可审计性丧失。** 向量搜索的结果难以解释——为什么这条记录的 cosine similarity 是 0.87，而那条是 0.83？调试和优化非常困难。本套工具的规则驱动检索路径可以被完整追踪（mem-ace-playbook 的 retrieval-log.md） |
| **为什么分桶用 8 个，而不是更多或更少？** | 这个数字来自对 AdaMem 四层分类和 Memory Survey 五层分类的综合裁剪，可以根据需要做调整：<br><br>太少（如 3 个桶）：不同类型记忆混在一起，检索时还是需要 LLM 做二次区分<br><br>太多（如 20 个桶）：增加了用户的认知负担，分类规则也更难维护<br><br>8 个：覆盖了记忆的所有主要类型（人物/偏好/事件/技能/工作状态/知识三类），每个桶的边界清晰，分类规则可以写成确定性规则 |
| **为什么 Persona 层要求"两次以上佐证"？** | 这是防止 Agent 过度自信写入错误信息的关键机制。<br><br>如果 Agent 从一次对话中推断"用户不喜欢表情符号"，立即写入 Persona，那么当这个推断是错误的时候，它会反复影响后续的所有对话。Persona 是高稳定性的人格特质，修改成本高，写错了危害大。<br><br>通过要求至少两次独立事件的佐证，大幅降低了单次偶发行为被误认为稳定特质的风险。 |
| **会不会破坏我现有的 memory 文件？** | 不会。原始 `.md` 文件完全不动，`.adaptr-v1/` 是新增目录。首次全量重构（bootstrap）还会自动备份旧数据到同级目录。 |
| **首次运行很慢，正常吗？** | 正常。首次是 `bootstrap` 全量扫描，时间取决于你的 memory 文件数量，一般 1~3 分钟。后续走 `ingest` 只处理变更文件，速度会快很多。 |
| **看到** `**Memory Migration Warning**`**，怎么办？** | 查看 `last_status.json` 的 `summary.status` 字段确认，再看 `last_status.md` 里的 `Error code / Error detail / Hints`，按提示操作。最常见的原因是 bootstrap 事件没有携带可信工作区路径（安全跳过，不是出错）。`MEMORY_KNOWLEDGE_AUTO_MIGRATE.md` 是虚拟注入文件，磁盘上不存在，不要用全盘搜索去找它。 |
| **会依赖 localhost embedding 服务吗？** | 不依赖。Python 侧只用标准库，检索增强去重直接调 OpenClaw 原生 `memory search`，不需要任何外部 embedding HTTP 服务。 |
| **去重是怎么做的？可以审计吗？** | 增量入库时，新记录先走 OpenClaw `memory search`，相似度超阈值的内容被拒绝入库。每次增量运行都会生成 `reports/dedup_audit_*.jsonl`，可以直接查看哪些记录因何被拒。 |
| **hook 是一直在跑吗？会影响性能？** | 只在 `agent:bootstrap` 触发，即你**开新会话**时跑一次。subagent session 自动跳过，不会因 subagent 频繁创建而反复执行。 |
| **archive 里的内容还能找到吗？** | 可以。`viking/archive/records.jsonl` 保留了所有归档记录，没有删除。只是不再进入主检索索引，避免干扰日常召回。 |
| **支持哪些文件格式？** | 支持 `.md / .txt / .log / .yaml / .yml / .json / .jsonl / .csv / .db / .sqlite`。其中 `.abstract.md` 和 `.overview.md` 会自动识别为索引文件跳过，不重复入库。 |
| **新会话提示"另有迁移进程在运行"？** | v7 之前，如果脚本崩溃或被强制终止，会留下孤儿锁文件（`.auto_migrate.lock`），需手动删除。v7 新增了自动清理：检查锁文件中记录的 PID 是否仍存活，若进程已不在或锁超过 300 秒，自动释放。正常情况下不再需要手动干预。 |
| **为什么有两份 scripts？** | handler.js 按 `__dirname/scripts/auto_migrate.py` 查找脚本（hook 路径），skill 系统按 `skills/openclaw-memory-knowledge/scripts/` 查找（skill 路径）。两个入口必须都存在，内容始终保持一致。 |
| LLM 增强开了多少环节？ | v7.1 开始共 8 个 LLM 增强环节：分类兜底、语义切块、去重灰区裁决、检索后处理、写入质量门控、实体+关系联合抽取、自修复阶段的记忆合并、矛盾检测。全部为可选增强，**LLM 不可用时静默回退规则**。 |
| 记忆会越来越多吗？ | v7 之前只做版本替换（同文件改了就更新），但不同文件描述同一件事仍会累积。v7 新增记忆合并：自修复阶段检测同 bucket 内高相似记录，LLM 合并为一条精练记忆，主动收敛碎片化。 |

---

## 七、依赖说明

|项目|要求|
|---|---|
|Python|标准库，无需 `pip install`|
|OpenClaw|需要可用的 `memory search`|
|外部 embedding 服务|❌ 不需要|
|特殊系统权限|❌ 不需要|

---

## 八、文件结构速查

```
memory-knowledge-auto-migrate-hook-v7.zip
└── memory-knowledge-auto-migrate/
    ├── HOOK.md                    # Hook 元信息（触发事件、描述）
    ├── INSTALL.md                 # 安装说明（权威入口）
    ├── SKILL.md                   # Skill 总览（根级副本）
    ├── handler.js                 # Hook 入口（agent:bootstrap 触发）
    ├── scripts/                   # ← handler.js 优先从这里加载脚本
    │   ├── auto_migrate.py           # 零配置入口（首次/增量自动切换）
    │   ├── bootstrap_restructure.py  # 全量重构
    │   ├── incremental_ingest.py     # 增量入库
    │   ├── self_evolve.py            # 自修复
    │   ├── mk_arch_core.py           # 核心库
    │   ├── native_memory_search.py   # 原生检索封装
    │   ├── llm_backend.py            # LLM 增强后端（v3 新增）
    │   └── install_hook_from_zip.sh  # 便捷安装脚本
    └── skills/
        └── openclaw-memory-knowledge/
            ├── SKILL.md           # Skill 总览
            ├── INSTALL.md
            ├── requirements.txt
            ├── scripts/           # ← skill 系统从这里加载（与根级 scripts/ 内容相同）
            │   └── (同上，完整副本)
            └── references/
                ├── architecture.md           # 架构详细说明
                ├── mapping.yaml              # 桶路径映射配置
                ├── zero-config.md            # 零配置使用说明
                └── retrieval-protocol.md     # 检索协议说明（v5 新增）
```

> **为什么有两份 scripts？** handler.js 按 `__dirname/scripts/auto_migrate.py` 查找脚本（hook 路径），skill 系统按 `skills/openclaw-memory-knowledge/scripts/` 查找（skill 路径）。两个入口必须都存在，内容始终保持一致。

## 九、LLM 增强（可选）

v3.0 起，工具内置了基于 OpenClaw 的 LLM 增强能力（`llm_backend.py`）。安装后无需额外配置，自动从 `~/.openclaw/agents/main/agent/models.json` 发现 OpenClaw 配置。

> **v7 重要修复**：v3–v6.1 存在一个 P1 级 bug——hook 自动模式下，`_get_llm()` 始终返回 NoopLLMBackend，导致下面五个增强环节在自动运行时**全部不生效**，只有手动执行脚本才能触发。v7 修复了该问题：每个子进程首次调用 `_get_llm()` 时自动从 `llm_backend` 模块发现并初始化 OpenClawLLMBackend，五个增强环节现在在 hook 自动模式下正常工作。

### 五个增强环节

|环节|触发条件|效果|
|---|---|---|
|分类兜底|规则 confidence < 0.7|语义分类取代关键词猜测，避免"秋实喜欢深夜写代码"被分到 working|
|语义切块|无标题结构 + 文本 > 100 字|无结构流水账按语义边界切成独立记忆单元，不机械按段落切|
|去重灰区裁决|相似度 score 在 0.55–0.85|模型判断 duplicate/distinct，避免"相似但不同"被误删|
|检索后处理|每次 memory search 返回结果后|过滤不相关片段 + 合并语义重复 + 生成摘要，喂给 Agent 的是整理好的上下文|
|写入质量门控|文本 50–200 字且规则未拒绝|模型判断是否值得长期存储，过滤系统日志、无意义输出|

### 降级保证

LLM 不可用时（models.json 未发现 / 网络超时 / API 报错），所有环节静默回退到规则逻辑。功能完整，质量降级。`stderr` 会输出提示：`LLM backend 未配置，已回退规则模式`。

### 环境变量覆盖（非美团内部用户）

```
export OpenClaw_LLM_BASE_URL="https://your-endpoint/v1"
export OpenClaw_LLM_API_KEY="your-key"
export OpenClaw_LLM_MODEL="your-model"
```

## 附：版本历史

|版本|时间|变更|
|---|---|---|
|v1.0|2026-03-24|初版：bootstrap 重构 + hook 自动触发|
|v2.0|2026-03-25|增量 UPDATE 语义版本替换、去重审计日志、TTL/热度归档、关系图谱质量增强（谓词归一化、relation_decisions、弱关系自修复）、workspace 自动发现安全加固|
|v3.0|2026-03-27|LLM 增强能力（llm_backend.py）：零 pip 依赖，自动从 models.json 发现 OpenClaw 配置；五个增强环节（分类兜底、语义切块、去重灰区裁决、检索后处理、写入质量门控）；降级保证（LLM 不可用时全链路回退规则）|
|v4.0|2026-03-28|rerank 接入 native_memory_search.py：检索后处理（过滤不相关 + 合并重复 + 生成摘要）挂入主检索调用链；auto_migrate.py summary 新增 llm_backend 字段|
|v5.0|2026-03-29|① SKILL.md Hook 自动模式加 ⚠️ 不要手动跑脚本说明；② SKILL.md 新增「检索结果使用规则」（rerank.ok==true 时优先用 rerank.summary）；③ auto_migrate.py summary 新增 duration_seconds + processed_files；④ INSTALL.md 安装验证扩充为 4 步；⑤ llm_backend.py 清空内网 default_base_url，未配置时立即打 stderr 降级提示；⑥ P0 治理字段（memory_function/formation_mode/trust_tier/memory_policy）；⑦ Progressive Disclosure 检索协议 + profile_snapshot/preferences_snapshot 快照层|
|v6.0|2026-03-31|① handler.js 新增 persistLastStatus()：每次 bootstrap 无论客户端是否支持注入，均落盘 last_status.md（人读）+ last_status.json（机器读）；② handler.js 加 bootstrapFiles = [] 兜底；③ processed_files 按运行模式区分；④ 全部文档改为以 last_status.json.summary.target_root 为准|
|v6.1|2026-04-01|① handler.js getOpenclawHome() 路径偏移修复：当 OPENCLAW_HOME 指向 HOME 目录（如 /root）且 HOME/.openclaw 存在时，优先使用 HOME/.openclaw，修复 skill sync 装到 /root/skills/ 而非 ~/.openclaw/skills/ 的问题；② INSTALL.md Step 3/5 路径写死修复：改为动态 Python 定位脚本，探测候选路径列表，不再写死 ~/.openclaw/skills/；③ INSTALL.md 新增 skill sync 路径偏移排查指引：SKILL_DIR=NOT_FOUND 时说明如何检查 $OPENCLAW_HOME、建议升级 v6.1+、提供临时绕过 find 命令；④ 便捷安装方式改用 $SKILL_DIR 变量|
|v7.0|2026-04-03|**LLM 链路修复（P1）**：① mk_arch_core.`_get_llm()` / incremental_ingest.`_get_ingest_llm()` 改为懒加载自动发现——每个子进程首次调用时独立初始化 OpenClawLLMBackend，修复 v3–v6.1 hook 自动模式下五个 LLM 增强环节实际全部不生效的问题；② bootstrap_restructure / incremental_ingest 的 report 新增 `llm_backend` 字段，auto_migrate.py 优先采信子进程报告的实际 backend（不再依赖父进程检测），summary 中 `llm_backend` 字段现在可信；**稳定性**：③ auto_migrate.py 新增 `_is_stale_lock()`：PID 存活检查 + 300s mtime 超时双重判断，自动清理崩溃遗留孤儿锁，不再需要手动删 `.auto_migrate.lock`；④ handler.js `targetRoot` fallback 改为检查 `summary.target_root` 实际存在性，fallback 时优先探测 `memory/` 子目录决定 `.adaptr-v1` 位置；**性能 & 清理**：⑤ `should_reingest` 新增 mtime+size 快速路径，未变化时跳过 SHA256 全文读取（大文件友好）；⑥ write_jsonl 修复空列表语义：`append=False` 时正确清空文件而非静默跳过；⑦ 删除 16 个 `semantic_*` 重复统计字段、冗余 `import json as _json`、内联 `__import__('time')` 替换为标准 import|
