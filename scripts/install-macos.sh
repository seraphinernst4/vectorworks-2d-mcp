#!/bin/bash
# Keystone for Vectorworks - Installer für macOS
#   curl -LsSf https://raw.githubusercontent.com/seraphinernst4/vectorworks-2d-mcp/main/scripts/install-macos.sh | bash
# Lädt die Claude-Erweiterung, räumt eine alte Einrichtung ("vectorworks")
# auf und öffnet die Erweiterung in Claude Desktop zum Installieren.
# Optionen (Umgebungsvariablen):
#   VWMCP_MCPB_URL=<url>     andere Quelle der Erweiterung
#   VWMCP_SOURCE=<spec>      Quelle des Servers für Claude Code (Standard: GitHub main)
set -euo pipefail

RAW="https://raw.githubusercontent.com/seraphinernst4/vectorworks-2d-mcp/main"
MCPB_URL="${VWMCP_MCPB_URL:-$RAW/release/Keystone-for-Vectorworks.mcpb}"
SOURCE="${VWMCP_SOURCE:-https://github.com/seraphinernst4/vectorworks-2d-mcp/archive/refs/heads/main.zip}"
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

find_uvx() {
  if command -v uvx >/dev/null 2>&1; then command -v uvx
  elif [ -x "$HOME/.local/bin/uvx" ]; then echo "$HOME/.local/bin/uvx"
  fi
}

say "1/3  Erweiterung herunterladen"
DIR="$HOME/Downloads"; [ -d "$DIR" ] || DIR="$HOME"
MCPB="$DIR/Keystone-for-Vectorworks.mcpb"
curl -fL --progress-bar -o "$MCPB" "$MCPB_URL"
echo "Gespeichert: $MCPB"

say "2/3  Alte Einrichtung entfernen (falls vorhanden)"
if [ -f "$CFG" ] && grep -q '"vectorworks"[[:space:]]*:' "$CFG"; then
  UVX="$(find_uvx || true)"
  if [ -n "$UVX" ]; then
    # A running Claude Desktop writes its own copy of the config when it
    # quits, which would undo this change: quit it first (it reopens below).
    if pgrep -xq "Claude"; then
      echo "Claude Desktop wird kurz beendet (sonst überschreibt es die Einstellung) ..."
      osascript -e 'tell application "Claude" to quit' >/dev/null 2>&1 || true
      for _ in $(seq 1 30); do pgrep -xq "Claude" || break; sleep 1; done
    fi
    cp "$CFG" "$CFG.bak-vwmcp"
    "$UVX" python - "$CFG" <<'PY'
import json, sys, pathlib
path = pathlib.Path(sys.argv[1])
cfg = json.loads(path.read_text())
cfg.get("mcpServers", {}).pop("vectorworks", None)
path.write_text(json.dumps(cfg, indent=2) + "\n")
print(f"Alter Eintrag 'vectorworks' entfernt (Sicherung: {path}.bak-vwmcp)")
PY
  else
    echo "Bitte in Claude ▸ Einstellungen ▸ Entwickler ▸ Konfiguration bearbeiten"
    echo "den Eintrag \"vectorworks\" unter \"mcpServers\" löschen."
  fi
else
  echo "Nichts zu tun."
fi
if command -v claude >/dev/null 2>&1; then
  # Claude Code: same server under the new name, bridge kept up to date.
  UVX="$(find_uvx || true)"
  if [ -z "$UVX" ]; then
    echo "Installiere uv von astral.sh (für Claude Code) ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UVX="$HOME/.local/bin/uvx"
  fi
  claude mcp remove --scope user vectorworks >/dev/null 2>&1 || true
  claude mcp remove --scope user keystone-vectorworks >/dev/null 2>&1 || true
  # name before -e: --env takes several values and would swallow it
  if claude mcp add --scope user keystone-vectorworks -e VWMCP_AUTO_BRIDGE=1 -- "$UVX" --refresh --from "$SOURCE" vwmcp; then
    echo "Claude Code: 'keystone-vectorworks' eingetragen."
  else
    echo "Claude Code: Eintragen fehlgeschlagen (siehe docs/claude-config.md) - weiter mit Claude Desktop."
  fi
fi

say "3/3  In Claude Desktop installieren"
# Prepare Python and the packages now (same environment the extension uses),
# so the first start in Claude Desktop is quick.
UV=""
if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"
elif [ -x "$HOME/.vwmcp/uv/uv" ]; then UV="$HOME/.vwmcp/uv/uv"
fi
if [ -n "$UV" ] && command -v unzip >/dev/null 2>&1; then
  echo "Bereite Python und Pakete vor (beim ersten Mal 1-2 Minuten) ..."
  TMP="$(mktemp -d)"
  unzip -q "$MCPB" -d "$TMP"
  if UV_PROJECT_ENVIRONMENT="$HOME/.vwmcp/venv" UV_PYTHON_PREFERENCE=only-managed \
      "$UV" sync --project "$TMP" --no-dev --quiet; then
    echo "Fertig."
  else
    echo "Vorbereitung übersprungen - die Erweiterung lädt beim ersten Start selbst."
  fi
  rm -rf "$TMP"
fi
if [ -d "/Applications/Claude.app" ] || [ -d "$HOME/Applications/Claude.app" ]; then
  open -a "Claude" "$MCPB"
  echo "Claude Desktop zeigt jetzt \"Keystone for Vectorworks\": auf \"Installieren\" klicken."
else
  echo "Claude Desktop nicht gefunden. Installiere es und öffne dann $MCPB per Doppelklick."
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

Die Brücke in Vectorworks legt die Erweiterung beim Start selbst an. Beim
ersten Zugriff fragt macOS, ob Claude Vectorworks steuern darf: "OK" klicken.
MSG
