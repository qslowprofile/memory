#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_NAME="$(basename "$SKILL_ROOT")"
OUT_DIR="${1:-$SKILL_ROOT/dist}"
mkdir -p "$OUT_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
ZIP_PATH="$OUT_DIR/${SKILL_NAME}-${STAMP}.zip"

(
  cd "$(dirname "$SKILL_ROOT")"
  zip -r "$ZIP_PATH" "$SKILL_NAME" \
    -x "*/__pycache__/*" "*/.DS_Store" "*/dist/*" "*/.git/*" >/dev/null
)

echo "$ZIP_PATH"
