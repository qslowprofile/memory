---
name: memory-knowledge-auto-migrate
description: "Auto-migrates native OpenClaw memory/knowledge into layered architecture on bootstrap"
metadata: {"openclaw":{"emoji":"🧠","events":["agent:bootstrap"]}}
---

# Memory Knowledge Auto Migrate Hook

在 `agent:bootstrap` 事件自动触发：

- 自动将内置 skill 同步到 `~/.openclaw/skills/openclaw-memory-knowledge`
- 首次：执行 bootstrap 重构
- 后续：执行 incremental 增量入库
- apply 后（有实质变更时）：执行 self-evolve 质检与安全修复
- 若事件中无法解析 workspace，会安全跳过并给出排障提示（避免误写入错误目录）

不需要手填 memory/knowledge 路径。
