#!/usr/bin/env bash
# Package the agent skill as a ZIP for Claude Desktop / claude.ai.
# The ZIP must contain the skill folder as its root entry.
set -euo pipefail

cd "$(dirname "$0")"
SKILL=blogger-publishing
OUT="dist/${SKILL}.zip"

mkdir -p dist
rm -f "$OUT"
find skills -name '.DS_Store' -delete
(cd skills && zip -r -q "../${OUT}" "$SKILL" -x '*.DS_Store')

echo "built ${OUT}"
unzip -l "$OUT"
