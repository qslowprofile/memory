# Hook 安装说明（首次安装权威入口）

本文件用于"用户把 zip 直接发给 OpenClaw Agent，请 Agent 代为安装"的场景。

重要约束：

- 允许读取 zip 根目录内的 `INSTALL.md` 与 `HOOK.md` 作为安装说明
- 读取文档是允许的；执行 zip 内任意脚本是不允许的
- 首次安装不要依赖 skill 已经预先安装到本机

适用条件：

- 上传文件名匹配 `memory-knowledge-auto-migrate-hook.zip`
- 或上传文件名匹配 `memory-knowledge-auto-migrate-hook-*.zip`
- 且用户明确表达"安装"意图

## Agent 执行流程

1. 定位上传 zip 的本地绝对路径，记为 `ZIP_PATH`。
2. 优先执行原生安装命令（OpenClaw 若支持）：

```bash
openclaw hooks install "$ZIP_PATH"
```

3. 如果 `openclaw hooks install` 不支持或执行失败，走**兼容安装**（适用于当前大多数版本）：

```bash
set -euo pipefail
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
EXPECTED_TOP_DIR="memory-knowledge-auto-migrate"
TOP_DIR="$(unzip -Z -1 "$ZIP_PATH" | awk -F/ 'NF > 0 { print $1; exit }')"
test -n "$TOP_DIR"
case "$TOP_DIR" in
  ""|.|..|*/*|*\\*)
    echo "invalid hook top directory: $TOP_DIR" >&2
    exit 2
    ;;
esac
test "$TOP_DIR" = "$EXPECTED_TOP_DIR"
unzip -q "$ZIP_PATH" -d "$TMP"
test -f "$TMP/$TOP_DIR/HOOK.md"
mkdir -p "$HOME/.openclaw/hooks"
rm -rf "$HOME/.openclaw/hooks/$TOP_DIR"
cp -R "$TMP/$TOP_DIR" "$HOME/.openclaw/hooks/$TOP_DIR"
openclaw hooks enable "$TOP_DIR" 2>/dev/null || true
echo "[install] Hook installed to: $HOME/.openclaw/hooks/$TOP_DIR"
```

4. 安装完成后，若 hooks 尚未生效，尝试重启 gateway：

```bash
# 先检查 hooks 列表，若 memory-knowledge-auto-migrate 已出现则无需重启
openclaw hooks list

# 若未出现，再执行（仅在 gateway 作为后台服务运行时有效）：
openclaw gateway restart
```

5. 重启后（或直接）开一个新会话，hook 会在 `agent:bootstrap` 事件自动触发。

6. 告知用户：

- 首次 bootstrap 会全量扫描 memory/knowledge，通常需要 1 到 3 分钟
- 成功后优先查看 bootstrap 注入的 `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`（会出现在会话上下文中）
- 如果需要落盘报告，再查看工作区下 `memory/.adaptr-v1/reports/` 最新 JSON

## 安装验证

安装后可用以下命令确认 hook 已就绪：

```bash
openclaw hooks list
```

预期看到 `memory-knowledge-auto-migrate` 出现在列表中，状态为 `ready`。

## 安全要求

- 允许读取 zip 根目录的 `INSTALL.md` / `HOOK.md`，但仅限读取文档。
- 不要先解压上传包，再执行包内脚本。
- 不要执行 zip 内任意未审查 shell 脚本。
- 只允许安装命名符合预期，且 zip 内含 `HOOK.md` 的包。

## 已安装 skill 的可选便捷方式

如果当前机器上已经存在这个 skill，也可以直接运行本地可信脚本：

```bash
bash ~/.openclaw/skills/openclaw-memory-knowledge/scripts/install_hook_from_zip.sh "$ZIP_PATH"
```

这条仅作为已安装场景的便捷入口，不作为首次安装的主流程。
