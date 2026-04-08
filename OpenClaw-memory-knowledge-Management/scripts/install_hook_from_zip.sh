#!/usr/bin/env bash
set -euo pipefail

ZIP_PATH="${1:-}"
HOOK_NAME_DEFAULT="${2:-memory-knowledge-auto-migrate}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
ZIP_NAME_PATTERN='^memory-knowledge-auto-migrate-hook(-.*)?\.zip$'
EXPECTED_TOP_DIR="memory-knowledge-auto-migrate"

if [[ -z "$ZIP_PATH" ]]; then
  echo "[install] usage: bash scripts/install_hook_from_zip.sh /path/to/memory-knowledge-auto-migrate-hook.zip [hook-name]" >&2
  exit 2
fi

ZIP_ABS="$(cd "$(dirname "$ZIP_PATH")" && pwd)/$(basename "$ZIP_PATH")"
if [[ ! -f "$ZIP_ABS" ]]; then
  echo "[install] zip not found: $ZIP_ABS" >&2
  exit 2
fi

ZIP_BASENAME="$(basename "$ZIP_ABS")"
if [[ ! "$ZIP_BASENAME" =~ $ZIP_NAME_PATTERN ]]; then
  echo "[install] unexpected zip filename: $ZIP_BASENAME" >&2
  echo "[install] expected pattern: memory-knowledge-auto-migrate-hook.zip or memory-knowledge-auto-migrate-hook-*.zip" >&2
  exit 2
fi

if ! unzip -Z -1 "$ZIP_ABS" | grep -Eq '/HOOK\.md$'; then
  echo "[install] invalid hook zip (HOOK.md missing): $ZIP_ABS" >&2
  exit 2
fi

TOP_DIR="$(unzip -Z -1 "$ZIP_ABS" | awk -F/ 'NF>0 {print $1; exit}')"
if [[ -z "$TOP_DIR" ]]; then
  echo "[install] invalid hook zip (missing top directory): $ZIP_ABS" >&2
  exit 2
fi
case "$TOP_DIR" in
  ""|.|..|*/*|*\\*)
    echo "[install] invalid hook top directory: $TOP_DIR" >&2
    exit 2
    ;;
esac
if [[ "$TOP_DIR" != "$EXPECTED_TOP_DIR" ]]; then
  echo "[install] unexpected hook top directory: $TOP_DIR" >&2
  echo "[install] expected top directory: $EXPECTED_TOP_DIR" >&2
  exit 2
fi
HOOK_NAME="$HOOK_NAME_DEFAULT"
if [[ -n "$TOP_DIR" ]]; then
  HOOK_NAME="$TOP_DIR"
fi

INSTALL_METHOD=""
ENABLE_STATUS="skipped"
ENABLE_MESSAGE=""

has_openclaw="false"
if command -v openclaw >/dev/null 2>&1; then
  has_openclaw="true"
fi

if [[ "$has_openclaw" == "true" ]]; then
  if openclaw hooks --help 2>/dev/null | grep -Eq '(^|[[:space:]])install([[:space:]]|$)'; then
    if openclaw hooks install "$ZIP_ABS"; then
      INSTALL_METHOD="openclaw_hooks_install"
    fi
  fi
fi

if [[ -z "$INSTALL_METHOD" ]]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  unzip -q "$ZIP_ABS" -d "$TMP"
  if [[ ! -f "$TMP/$TOP_DIR/HOOK.md" ]]; then
    echo "[install] invalid hook zip (HOOK.md missing after extract): $ZIP_ABS" >&2
    exit 2
  fi
  mkdir -p "$OPENCLAW_HOME/hooks"
  rm -rf "$OPENCLAW_HOME/hooks/$TOP_DIR"
  cp -R "$TMP/$TOP_DIR" "$OPENCLAW_HOME/hooks/$TOP_DIR"
  INSTALL_METHOD="fallback_copy"
fi

if [[ "$has_openclaw" == "true" ]]; then
  if openclaw hooks enable "$HOOK_NAME" >/dev/null 2>&1; then
    ENABLE_STATUS="ok"
  else
    ENABLE_STATUS="failed"
    ENABLE_MESSAGE="openclaw hooks enable $HOOK_NAME failed; you may need to enable it manually."
  fi
else
  ENABLE_STATUS="skipped"
  ENABLE_MESSAGE="openclaw command not found; skip enable step."
fi

HOOK_DIR="$OPENCLAW_HOME/hooks/$HOOK_NAME"
if [[ ! -d "$HOOK_DIR" ]]; then
  # 某些实现会在 hooks install 后写入其它目录，尽量给出可观测信息。
  HOOK_DIR="(not found under $OPENCLAW_HOME/hooks; maybe managed by OpenClaw runtime)"
fi

echo "[install] ok"
echo "[install] zip: $ZIP_ABS"
echo "[install] method: $INSTALL_METHOD"
echo "[install] hook_name: $HOOK_NAME"
echo "[install] hook_dir: $HOOK_DIR"
echo "[install] enable_status: $ENABLE_STATUS"
if [[ -n "$ENABLE_MESSAGE" ]]; then
  echo "[install] enable_message: $ENABLE_MESSAGE"
fi
echo "[install] next: start a new session to trigger agent:bootstrap"
echo "[install] note: first bootstrap usually takes 1-3 minutes"
echo "[install] verify: check MEMORY_KNOWLEDGE_AUTO_MIGRATE.md or .adaptr-v1/reports/ after bootstrap"
