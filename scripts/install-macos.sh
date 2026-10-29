#!/bin/bash
# Vectorworks 2D MCP - Installer für macOS
#   curl -LsSf https://raw.githubusercontent.com/seraphinernst4/vectorworks-2d-mcp/main/scripts/install-macos.sh | bash
# Optionen (Umgebungsvariablen):
#   VW_VERSION=2026          Vectorworks-Version (Ordnername), z. B. 2027
#   VWMCP_SOURCE=<spec>      Quelle des Servers (Standard: GitHub main)
set -euo pipefail

VW_VERSION="${VW_VERSION:-2026}"
SOURCE="${VWMCP_SOURCE:-git+https://github.com/seraphinernst4/vectorworks-2d-mcp}"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/4  uv (Python-Paketmanager) suchen"
if command -v uvx >/dev/null 2>&1; then
  UVX="$(command -v uvx)"
elif [ -x "$HOME/.local/bin/uvx" ]; then
  UVX="$HOME/.local/bin/uvx"
else
  echo "Installiere uv von astral.sh ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UVX="$HOME/.local/bin/uvx"
fi
echo "Verwende $UVX"

say "2/4  Brücke in den Vectorworks-Plug-in-Ordner kopieren (Vectorworks $VW_VERSION)"
"$UVX" --refresh --from "$SOURCE" vwmcp install-plugin --vw-version "$VW_VERSION"

say "3/4  In Claude Desktop eintragen"
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [ -d "$(dirname "$CFG")" ]; then
  [ -f "$CFG" ] && cp "$CFG" "$CFG.bak-vwmcp"
  "$UVX" python - "$CFG" "$UVX" "$SOURCE" "$VW_VERSION" <<'PY'
import json, sys, pathlib
path, uvx, source, version = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
cfg = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
entry = {"command": uvx, "args": ["--from", source, "vwmcp"]}
if version != "2026":
    entry["env"] = {"VWMCP_APP": f"Vectorworks {version}"}
cfg.setdefault("mcpServers", {})["vectorworks"] = entry
path.write_text(json.dumps(cfg, indent=2) + "\n")
print(f"'vectorworks' eingetragen in {path} (Sicherung: {path}.bak-vwmcp)")
PY
else
  echo "Claude Desktop nicht gefunden - übersprungen (siehe docs/claude-config.md)."
fi

say "4/4  In Claude Code eintragen (falls installiert)"
if command -v claude >/dev/null 2>&1; then
  claude mcp remove --scope user vectorworks >/dev/null 2>&1 || true
  if [ "$VW_VERSION" != "2026" ]; then
    claude mcp add --scope user -e VWMCP_APP="Vectorworks $VW_VERSION" vectorworks -- "$UVX" --from "$SOURCE" vwmcp
  else
    claude mcp add --scope user vectorworks -- "$UVX" --from "$SOURCE" vwmcp
  fi
else
  echo "Claude Code nicht gefunden - übersprungen."
fi

cat <<'MSG'

Fast fertig. Einmalig in Vectorworks (ca. 2 Minuten) - nur nötig, wenn du
den Menübefehl "MCP Run Job" noch nicht hast:

  1. Extras > Plug-ins > Plug-in-Manager... > Register "Eigene Plug-ins" > Neu...
     Typ: Befehl     Name: MCP Run Job     (genau so schreiben)
  2. Auswählen > Skript bearbeiten... > Sprache: Python, diese zwei Zeilen einfügen:

         from vwmcp import vwmcp_bridge
         vwmcp_bridge.run_pending()

  3. Extras > Arbeitsumgebungen > Aktuelle Arbeitsumgebung bearbeiten... > Register "Menüs":
     "MCP Run Job" (unter "Sonstiges") in ein Menü ziehen, z. B. "Extras".

Dann Claude Desktop beenden und neu öffnen. Beim ersten Zugriff fragt macOS,
ob Claude Vectorworks steuern darf: "OK" klicken.
MSG
