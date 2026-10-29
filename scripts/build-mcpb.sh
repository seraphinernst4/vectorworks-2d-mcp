#!/bin/bash
# Builds the Claude Desktop extension release/Keystone-for-Vectorworks.mcpb
# from mcpb/ (manifest, bundle pyproject, entry point) + src/vwmcp + the bridge.
#   scripts/build-mcpb.sh            (needs node/npx for validation and packing)
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/vwmcp/__init__.py)"
STAGE="build/mcpb"
OUT="release/Keystone-for-Vectorworks.mcpb"

rm -rf "$STAGE" && mkdir -p "$STAGE/server" release
sed "s/@VERSION@/$VERSION/" mcpb/manifest.json > "$STAGE/manifest.json"
sed "s/@VERSION@/$VERSION/" mcpb/pyproject.toml > "$STAGE/pyproject.toml"
cp mcpb/.mcpbignore "$STAGE/.mcpbignore"
cp mcpb/server/main.py mcpb/server/index.js "$STAGE/server/"
cp src/vwmcp/assets/icon.png "$STAGE/icon.png"
cp LICENSE "$STAGE/LICENSE"
# server code without caches
(cd src && find vwmcp -name '__pycache__' -prune -o -type f ! -name '*.pyc' -print) |
  while read -r f; do mkdir -p "$STAGE/server/$(dirname "$f")"; cp "src/$f" "$STAGE/server/$f"; done
mkdir -p "$STAGE/server/vwmcp/_plugin"
cp vw-plugin/vwmcp/vwmcp_bridge.py "$STAGE/server/vwmcp/_plugin/vwmcp_bridge.py"
# handshake answers of exactly this server, for the launcher's instant start
PYTHON="${PYTHON:-uv run --quiet python}"
$PYTHON scripts/dump_static.py "$STAGE/server" "$STAGE/server/static.json"

MCPB="npx -y @anthropic-ai/mcpb@2.1.2"
$MCPB validate "$STAGE/manifest.json"
rm -f "$OUT"
$MCPB pack "$STAGE" "$OUT"
echo "Built $OUT ($VERSION)"
