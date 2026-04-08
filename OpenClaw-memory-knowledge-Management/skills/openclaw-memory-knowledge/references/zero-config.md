# 零配置使用说明

## 1. 普通用户默认用法

普通用户只需要做两件事：

1. 把 `memory-knowledge-auto-migrate-hook.zip` 发给自己的 OpenClaw Agent
2. 明确说“安装这个 hook”

Agent 应统一按同包 `INSTALL.md` 执行安装。

首次安装允许读取 zip 根目录里的 `INSTALL.md` / `HOOK.md` 获取说明。
首次安装阶段不要依赖相对路径脚本，也不要自行解压后执行包内脚本。

## 2. 安装后的自动行为

安装完成后，开一个新会话触发 `agent:bootstrap`。

系统会自动：

- 同步 skill payload 到 `~/.openclaw/skills/openclaw-memory-knowledge`
- 自动发现工作区
- 首次做全量重构
- 后续做增量入库
- 仅在有实质变更时执行 `self_evolve`

首次 bootstrap 一般需要 1 到 3 分钟。

## 3. 如何判断是否成功

优先看这两个地方：

- bootstrap 注入文件 `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`
- 工作区下 `.adaptr-v1/reports/` 最新报告

P0 版本新增的派生层也值得一起看：

- `layers/profile_snapshot.json`
- `layers/preferences_snapshot.json`
- `layers/retrieval_protocol.json`

常见情况：

- `Memory Migration Done`：本次执行成功
- `Memory Migration Warning`：查看其中的 `Error code / Error detail / Hints`
- 没有报错但较慢：通常是首次全量扫描

## 4. 运行模式

- 自动模式：hook 在 `agent:bootstrap` 触发
- 手动模式：运行 `python scripts/auto_migrate.py`

手动模式默认也会自动发现 OpenClaw 工作区。
如果需要显式指定目录，再传 `--workspace-root`。

## 5. 常见问题

- 提示 `workspace_not_detected`
  说明 bootstrap 事件没有给出可信工作区，系统已安全跳过。补齐 `workspacePath`，或设置 `OPENCLAW_WORKSPACE`，或手动传 `--workspace-root`。
- 首次运行较慢
  首次 `bootstrap` 需要全量扫描；后续走 `ingest` 会快很多。
- 担心外部 embedding 依赖
  不依赖 `localhost:8080`。Python 侧仅使用标准库，检索增强去重依赖 OpenClaw 原生 `memory search`。
- 去重是否可审计
  可以。增量报告会生成 `reports/dedup_audit_*.jsonl`。
- 重构失败后如何排查
  先看 `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`，再看 `.adaptr-v1/reports/` 最新 JSON。
