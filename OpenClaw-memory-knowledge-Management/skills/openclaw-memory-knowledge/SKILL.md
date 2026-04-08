---
name: openclaw-memory-knowledge
description: 在 OpenClaw 中执行 memory/knowledge 的深度重构与持续增量入库。用于以下场景：已有 memory/knowledge 数据分散且结构混乱；需要一次性迁移到统一分层架构；需要后续新增数据自动按相同架构入库并去重；需要生成可审计报告以便排查与回滚。
triggers:
  - 用户上传文件名匹配 memory-knowledge-auto-migrate-hook.zip 或 memory-knowledge-auto-migrate-hook-*.zip 的压缩包，且明确表达“安装”意图
---

# OpenClaw Memory Knowledge

## 概览

这个 skill 负责两类事情：

1. 读取并重构已有 `memory/`、`knowledge/`，统一写入 AdaMem-lite + OpenViking 分层结构。
2. 对后续新增或变更内容做增量入库、版本替换、检索增强去重和自修复。

在当前版本里，除了原有 8 桶与 L0/L1/L2，还会额外生成：

- `layers/profile_snapshot.json`
- `layers/preferences_snapshot.json`
- `layers/retrieval_protocol.json`

并为每条 L2 记录补齐：

- `memory_function`
- `formation_mode`
- `trust_tier`
- `memory_policy`

默认运行入口：

```bash
python scripts/auto_migrate.py
```

这条命令会自动发现 OpenClaw 工作区并决定：

- 首次执行：`bootstrap`
- 后续执行：`ingest`
- apply 后：仅在有实质变更时执行 `self_evolve`

依赖边界：

- 不依赖任何外部 embedding HTTP 服务
- 检索增强去重依赖 OpenClaw 原生 `memory search`
- Python 侧无第三方 pip 依赖

## 首次安装

当用户上传 `memory-knowledge-auto-migrate-hook.zip` 或 `memory-knowledge-auto-migrate-hook-*.zip`，且明确要求安装时：

1. 首次安装统一按同包 `INSTALL.md` 执行。
2. 允许读取上传 zip 根目录的 `INSTALL.md` / `HOOK.md` 获取安装说明。
3. 不要先解压上传包，再执行包内脚本。
4. 不要在首次安装阶段依赖 `scripts/...` 相对路径，因为此时 skill 可能尚未安装到本机。

已安装场景下，才允许使用本地可信脚本：

```bash
bash ~/.openclaw/skills/openclaw-memory-knowledge/scripts/install_hook_from_zip.sh "$ZIP_PATH"
```

## Hook 自动模式

这是普通用户的默认模式，不需要手填 `memory/knowledge` 路径。

安装完成后：

1. 开一个新会话，触发 `agent:bootstrap`
2. hook 自动同步 skill payload 到 `~/.openclaw/skills/openclaw-memory-knowledge`
3. 自动执行 `scripts/auto_migrate.py`
4. 首次全量重构，后续自动增量入库

首次 bootstrap 通常需要 1 到 3 分钟。

## 可观测性

安装和首次运行完成后，优先查看：

- bootstrap 注入文件 `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`
- 工作区下 `.adaptr-v1/reports/` 最新报告
- `layers/l0_abstract.json`
- `layers/retrieval-hints.json`
- `layers/profile_snapshot.json`
- `layers/preferences_snapshot.json`
- `layers/retrieval_protocol.json`

常见状态判断：

- 看到 `Memory Migration Done`：表示 skill 同步与迁移均成功
- 看到 `Memory Migration Warning`：优先查看 `Error code / Error detail / Hints`
- 首次运行较慢但无报错：通常是正常的全量扫描阶段

## 手动模式

只有在不走 hook 自动模式时，才需要这一节。

### 自动发现工作区

```bash
python scripts/auto_migrate.py
```

如果自动发现失败，显式传入工作区根目录：

```bash
python scripts/auto_migrate.py --workspace-root /path/to/openclaw-or-workspace
```

### 手动预览

```bash
python scripts/auto_migrate.py --no-apply
```

### 强制全量重构

```bash
python scripts/auto_migrate.py --mode bootstrap
```

### 强制增量入库

```bash
python scripts/auto_migrate.py --mode ingest
```

### 显式按路径处理原始数据

仅在你要脱离原生 OpenClaw 工作区、手动指定源路径时使用：

```bash
python scripts/bootstrap_restructure.py \
  --memory-path /path/to/memory \
  --knowledge-path /path/to/knowledge \
  --target-root /path/to/arch_root
```

## 增量入库行为

`incremental_ingest.py` 会：

- 基于文件状态识别新增/变更文件
- 按 `(source_path, locator)` 做 UPDATE 语义替换
- 维护 `version`
- 调用 OpenClaw 原生 `memory search` 做检索增强去重
- 直接使用检索结果里的 `score` 判重
- 按 bucket 采用差异化阈值
- 输出去重审计日志到 `reports/dedup_audit_*.jsonl`
- 重建 bucket / layers / graph / state 物化视图

## P0 治理增强

当前版本还补了 4 个治理能力：

1. `memory_function / formation_mode / trust_tier / memory_policy`
2. profile / preferences current-state 快照
3. `private / ephemeral / persist` 持久化策略
4. 基于现有 L0/L1/L2 的 progressive disclosure 检索协议

其中：

- `memory_policy: private` 或 `<private>...</private>` 不进入长期层
- `memory_policy: ephemeral` 会被重路由到 `memory.working`
- policy 命中的记录会写入 `reports/policy_skipped_*.jsonl`

## 自修复行为

`self_evolve.py` 负责：

- L0/L1/L2 与 bucket 一致性检查
- 重复记录与失效引用检查
- TTL / 热度治理
- 图谱弱关系占比检测与重算
- 关系主值裁决一致性检查
- 归档、重建、去重、图谱重算等修复动作

手动执行：

```bash
python scripts/self_evolve.py --workspace-root /path/to/workspace --repair
```

## 资源

以下资源存在于已安装 skill payload 中，可直接调用：

- `INSTALL.md`
- `scripts/auto_migrate.py`
- `scripts/install_hook_from_zip.sh`
- `scripts/bootstrap_restructure.py`
- `scripts/incremental_ingest.py`
- `scripts/mk_arch_core.py`
- `scripts/native_memory_search.py`
- `scripts/self_evolve.py`
- `references/architecture.md`
- `references/retrieval-protocol.md`
- `references/mapping.yaml`
- `references/zero-config.md`

## 依赖说明

- Python：标准库即可
- OpenClaw：需要可用的 `memory search`
- 外部 embedding HTTP 服务：不需要
