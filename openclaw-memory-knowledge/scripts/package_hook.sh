#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$SKILL_ROOT/dist}"
mkdir -p "$OUT_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
BUILD_DIR="$OUT_DIR/.hook_build_$STAMP"
HOOK_DIR="$BUILD_DIR/memory-knowledge-auto-migrate"
mkdir -p "$HOOK_DIR/scripts"
SKILL_PAYLOAD_DIR="$HOOK_DIR/skills/openclaw-memory-knowledge"
mkdir -p "$SKILL_PAYLOAD_DIR/references" "$SKILL_PAYLOAD_DIR/scripts"

cp "$SKILL_ROOT/hooks/openclaw/HOOK.md" "$HOOK_DIR/HOOK.md"
cp "$SKILL_ROOT/README.md" "$HOOK_DIR/README.md"
cp "$SKILL_ROOT/INSTALL.md" "$HOOK_DIR/INSTALL.md"
cp "$SKILL_ROOT/hooks/openclaw/handler.js" "$HOOK_DIR/handler.js"
cp "$SKILL_ROOT/scripts/auto_migrate.py" "$HOOK_DIR/scripts/auto_migrate.py"
cp "$SKILL_ROOT/scripts/bootstrap_restructure.py" "$HOOK_DIR/scripts/bootstrap_restructure.py"
cp "$SKILL_ROOT/scripts/incremental_ingest.py" "$HOOK_DIR/scripts/incremental_ingest.py"
cp "$SKILL_ROOT/scripts/self_evolve.py" "$HOOK_DIR/scripts/self_evolve.py"
cp "$SKILL_ROOT/scripts/mk_arch_core.py" "$HOOK_DIR/scripts/mk_arch_core.py"
cp "$SKILL_ROOT/scripts/native_memory_search.py" "$HOOK_DIR/scripts/native_memory_search.py"
cp "$SKILL_ROOT/scripts/install_hook_from_zip.sh" "$HOOK_DIR/scripts/install_hook_from_zip.sh"

# Bundled skill payload (single-hook distribution model):
# the hook will sync this folder into ~/.openclaw/skills/openclaw-memory-knowledge.
cp "$SKILL_ROOT/SKILL.md" "$SKILL_PAYLOAD_DIR/SKILL.md"
cp "$SKILL_ROOT/INSTALL.md" "$SKILL_PAYLOAD_DIR/INSTALL.md"
cp "$SKILL_ROOT/requirements.txt" "$SKILL_PAYLOAD_DIR/requirements.txt"
cp "$SKILL_ROOT/references/architecture.md" "$SKILL_PAYLOAD_DIR/references/architecture.md"
cp "$SKILL_ROOT/references/mapping.yaml" "$SKILL_PAYLOAD_DIR/references/mapping.yaml"
cp "$SKILL_ROOT/references/retrieval-protocol.md" "$SKILL_PAYLOAD_DIR/references/retrieval-protocol.md"
cp "$SKILL_ROOT/references/zero-config.md" "$SKILL_PAYLOAD_DIR/references/zero-config.md"
cp "$SKILL_ROOT/scripts/auto_migrate.py" "$SKILL_PAYLOAD_DIR/scripts/auto_migrate.py"
cp "$SKILL_ROOT/scripts/bootstrap_restructure.py" "$SKILL_PAYLOAD_DIR/scripts/bootstrap_restructure.py"
cp "$SKILL_ROOT/scripts/incremental_ingest.py" "$SKILL_PAYLOAD_DIR/scripts/incremental_ingest.py"
cp "$SKILL_ROOT/scripts/self_evolve.py" "$SKILL_PAYLOAD_DIR/scripts/self_evolve.py"
cp "$SKILL_ROOT/scripts/mk_arch_core.py" "$SKILL_PAYLOAD_DIR/scripts/mk_arch_core.py"
cp "$SKILL_ROOT/scripts/native_memory_search.py" "$SKILL_PAYLOAD_DIR/scripts/native_memory_search.py"
cp "$SKILL_ROOT/scripts/install_hook_from_zip.sh" "$SKILL_PAYLOAD_DIR/scripts/install_hook_from_zip.sh"

TEMP_ZIP_PATH="$BUILD_DIR/memory-knowledge-auto-migrate-hook-$STAMP.zip"
FINAL_ZIP_PATH="$OUT_DIR/memory-knowledge-auto-migrate-hook.zip"
find "$OUT_DIR" -maxdepth 1 -type f -name 'memory-knowledge-auto-migrate-hook-*.zip' -delete
rm -f "$FINAL_ZIP_PATH"
(
  cd "$BUILD_DIR"
  zip -r "$TEMP_ZIP_PATH" memory-knowledge-auto-migrate >/dev/null
)

mv "$TEMP_ZIP_PATH" "$FINAL_ZIP_PATH"

rm -rf "$BUILD_DIR"
echo "$FINAL_ZIP_PATH"
