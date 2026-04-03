#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
HOOK_NAME="memory-knowledge-auto-migrate"
HOOK_SRC="$SKILL_ROOT/hooks/openclaw"
HOOK_DST="$OPENCLAW_HOME/hooks/$HOOK_NAME"
SCRIPTS_SRC="$SKILL_ROOT/scripts"
SCRIPTS_DST="$HOOK_DST/scripts"
MANAGED_SKILLS_DIR="$OPENCLAW_HOME/skills"
SKILL_NAME="openclaw-memory-knowledge"
SKILL_DST="$MANAGED_SKILLS_DIR/$SKILL_NAME"

mkdir -p "$OPENCLAW_HOME/hooks"
rm -rf "$HOOK_DST"
cp -r "$HOOK_SRC" "$HOOK_DST"
cp "$SKILL_ROOT/INSTALL.md" "$HOOK_DST/INSTALL.md"
mkdir -p "$SCRIPTS_DST"
cp "$SCRIPTS_SRC/auto_migrate.py" "$SCRIPTS_DST/auto_migrate.py"
cp "$SCRIPTS_SRC/bootstrap_restructure.py" "$SCRIPTS_DST/bootstrap_restructure.py"
cp "$SCRIPTS_SRC/incremental_ingest.py" "$SCRIPTS_DST/incremental_ingest.py"
cp "$SCRIPTS_SRC/self_evolve.py" "$SCRIPTS_DST/self_evolve.py"
cp "$SCRIPTS_SRC/mk_arch_core.py" "$SCRIPTS_DST/mk_arch_core.py"
cp "$SCRIPTS_SRC/native_memory_search.py" "$SCRIPTS_DST/native_memory_search.py"
cp "$SCRIPTS_SRC/install_hook_from_zip.sh" "$SCRIPTS_DST/install_hook_from_zip.sh"

echo "[install] hook copied to: $HOOK_DST"

mkdir -p "$SKILL_DST/references" "$SKILL_DST/scripts"
cp "$SKILL_ROOT/SKILL.md" "$SKILL_DST/SKILL.md"
cp "$SKILL_ROOT/INSTALL.md" "$SKILL_DST/INSTALL.md"
cp "$SKILL_ROOT/requirements.txt" "$SKILL_DST/requirements.txt"
cp "$SKILL_ROOT/references/architecture.md" "$SKILL_DST/references/architecture.md"
cp "$SKILL_ROOT/references/mapping.yaml" "$SKILL_DST/references/mapping.yaml"
cp "$SKILL_ROOT/references/retrieval-protocol.md" "$SKILL_DST/references/retrieval-protocol.md"
cp "$SKILL_ROOT/references/zero-config.md" "$SKILL_DST/references/zero-config.md"
cp "$SCRIPTS_SRC/auto_migrate.py" "$SKILL_DST/scripts/auto_migrate.py"
cp "$SCRIPTS_SRC/bootstrap_restructure.py" "$SKILL_DST/scripts/bootstrap_restructure.py"
cp "$SCRIPTS_SRC/incremental_ingest.py" "$SKILL_DST/scripts/incremental_ingest.py"
cp "$SCRIPTS_SRC/self_evolve.py" "$SKILL_DST/scripts/self_evolve.py"
cp "$SCRIPTS_SRC/mk_arch_core.py" "$SKILL_DST/scripts/mk_arch_core.py"
cp "$SCRIPTS_SRC/native_memory_search.py" "$SKILL_DST/scripts/native_memory_search.py"
cp "$SCRIPTS_SRC/install_hook_from_zip.sh" "$SKILL_DST/scripts/install_hook_from_zip.sh"
echo "[install] skill synced to: $SKILL_DST"

if command -v openclaw >/dev/null 2>&1; then
  openclaw hooks enable "$HOOK_NAME" || true
  echo "[install] requested openclaw to enable hook: $HOOK_NAME"
else
  echo "[install] openclaw command not found. Please enable hook manually:"
  echo "  openclaw hooks enable $HOOK_NAME"
fi

echo "[install] done"
