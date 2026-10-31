# Keystone for Vectorworks – Projektinfo

Alles Wissen zum Projekt an einer Stelle: was es ist, wie es aufgebaut ist,
wie man es installiert, baut und veröffentlicht, was getestet ist und was
offen ist. Stand: **31.10.2026, Version 0.3.6 (Beta)**.

## Auf einen Blick

| | |
|---|---|
| **Was** | Claude-Desktop-Erweiterung: Claude zeichnet exakte, sauber organisierte 2D-Architekturpläne in der offenen Vectorworks-Datei – lückenlos, ohne Überlappung, echte Bögen, jedes Objekt in Klasse und Ebene. Claude prüft und korrigiert selbst. |
| **Name in Claude** | „Keystone for Vectorworks“ (gleiches Icon wie Keystone for Rhino) |
| **Autor** | Seraphin Ernst – Fork von [giorgiomoroder/vectorworks-mcp](https://github.com/giorgiomoroder/vectorworks-mcp) |
| **Lizenz** | MIT (beide Urheber in `LICENSE`) |
| **Repo** | https://github.com/seraphinernst4/vectorworks-2d-mcp (öffentlich; Umbenennen in `keystone-vectorworks` bewusst gelassen) |
| **Erweiterung** | `release/Keystone-for-Vectorworks.mcpb` – Download: https://github.com/seraphinernst4/vectorworks-2d-mcp/raw/main/release/Keystone-for-Vectorworks.mcpb |
| **Installer** | `curl -LsSf https://raw.githubusercontent.com/seraphinernst4/vectorworks-2d-mcp/main/scripts/install-macos.sh \| bash` |
| **Voraussetzungen** | macOS, Vectorworks 2026, Claude-Desktop-App |
| **Getestet** | Vectorworks 2026 Update 7, deutsch (Ausbildungsversion), macOS, M2 Max |
| **Status** | Funktional fertig für 2D, live getestet. Noch nie auf einem fremden Mac frisch installiert; VW 2027 / englische Oberfläche ungetestet. |

## Installation auf einem fremden Mac

1. **Erweiterung:** Terminal öffnen, Installer-Befehl (oben) einfügen. Er lädt
   die `.mcpb`, holt uv nach `~/.vwmcp/uv` (ohne Admin-Rechte), bereitet
   Python und Pakete vor (~2 min), räumt eine alte Einrichtung „vectorworks“
   auf (Claude Desktop wird dafür kurz beendet, sonst überschreibt es die
   Konfiguration) und öffnet die Erweiterung in Claude → **Installieren**.
   Ohne Terminal: `.mcpb` laden und doppelklicken (erster Start dann 1–2 min
   länger).
2. **Vectorworks neu starten** – die Erweiterung legt die Brücke bei jedem
   Start nach `~/Library/Application Support/Vectorworks/2026/Plug-ins/vwmcp/`.
3. **Menübefehl „MCP Run Job“** (einmalig): Extras ▸ Plug-ins ▸
   Plug-in-Manager ▸ Eigene Plug-ins ▸ Neu… ▸ Typ **Befehl**, Name genau
   `MCP Run Job` ▸ Skript bearbeiten ▸ Python:
   ```python
   from vwmcp import vwmcp_bridge
   vwmcp_bridge.run_pending()
   ```
   Dann Extras ▸ Arbeitsumgebungen ▸ Aktuelle Arbeitsumgebung bearbeiten ▸
   Menüs: „MCP Run Job“ (unter *Sonstiges*) ins Menü **Extras** ziehen.
4. **Test:** Zeichnung offen, neuer Chat: „Ping Vectorworks.“ macOS fragt,
   ob Claude Vectorworks steuern darf → **OK**.

**Update:** Installer erneut ausführen (oder neue `.mcpb` doppelklicken) → in
Claude „Aktualisieren“. **Vectorworks 2027:** Claude ▸ Einstellungen ▸
Erweiterungen ▸ Keystone for Vectorworks ▸ Konfigurieren ▸ Version `2027`.

## Aufbau

```
Claude Desktop
  └─ Erweiterung (mcpb/, Node mit dem eingebauten Node von Claude)
       server/index.js   Starter: beantwortet server/discover, ping, initialize
                         und die erste tools/list SOFORT aus server/static.json,
                         reicht alles andere an Python weiter, findet/lädt uv
       └─ uv run server/main.py   (Python + Pakete in ~/.vwmcp/venv)
            └─ vwmcp (src/vwmcp)  MCP-Server, 28 Werkzeuge
                 ├─ geom/          rechnet auf dem Server: exakte Bögen, Prüfungen,
                 │                 Korrekturen, Prüfbild (shapely, matplotlib)
                 └─ Job-Ordner ~/.vwmcp/jobs → AppleScript ruft Menübefehl
                    „MCP Run Job“ → vwmcp_bridge.py (in Vectorworks, Python 3.9)
                    führt vwlib.py + vwlib2d.py aus → ~/.vwmcp/results
```

| Datei / Ordner | Inhalt |
|---|---|
| `src/vwmcp/vwlib2d.py` | Läuft *in* Vectorworks (Python 3.9!): Lesen, Zeichnen, Attribute, Klassen/Ebenen, Boolesche Operationen, Offset, Snapshot/Wiederherstellen |
| `src/vwmcp/geom/curves.py` | Exakte Bogen-Mathematik (Vectorworks-Bogenpunkt = Ausrundung, Tangentenpunkte), Diskretisierung |
| `src/vwmcp/geom/shapes.py` | Objekte → exakte Segmente/Flächen, Toleranzen, Punktindex |
| `src/vwmcp/geom/checks.py` | Alle Prüfungen (Überlappung, Spalt, knapp daneben, doppelt, Doppellinie, offen, Selbstschnitt, Punkte, Splitter, Klassen, Attribute) |
| `src/vwmcp/geom/fixes.py` | Korrekturen planen (fangen mit Kanten-Erhalt, schließen, bereinigen, Doppeltes, Überlappungen) |
| `src/vwmcp/geom/specs.py` | Eingaben → exakte Geometrie (Decimal, mm/cm/m, Maßketten, Pfade mit Bögen) |
| `src/vwmcp/geom/render.py` | Prüfbild |
| `src/vwmcp/tools/draw2d.py` | Die vw2d-Werkzeuge |
| `src/vwmcp/tools/generic.py` | Werkzeuge aus dem Original (ping, records, Symbole, `vw_exec_python`, `vw_revert_last`) |
| `src/vwmcp/journal.py` | Rückgängig-Journal `~/.vwmcp/journal.jsonl` |
| `src/vwmcp/app.py` | Server, Anweisungen an Claude (Zeichen- und Organisationsregeln), Icon |
| `vw-plugin/vwmcp/vwmcp_bridge.py` | Brücke in Vectorworks (unverändert vom Original, Version 0.1.0) |
| `mcpb/` | Erweiterung: `manifest.json`, `pyproject.toml` (Abhängigkeiten), `server/index.js`, `server/main.py` |
| `scripts/install-macos.sh` | Installer |
| `scripts/build-mcpb.sh`, `scripts/dump_static.py` | Bauen der `.mcpb` (inkl. `static.json` aus dem echten Server) |
| `skill/vectorworks-2d/SKILL.md` | Optionaler Skill (Arbeitsablauf, Regeln) |
| `docs/vs-notes.md` | Geprüftes Wissen über die Vectorworks-API (`vs.*`) |
| `tests/` | 110 Tests (Server-Logik; Code in Vectorworks wird live getestet) |

## Werkzeuge (28)

**2D:** `vw2d_structure` (Ebenen/Klassen + Organisationsprobleme), `vw2d_read`,
`vw2d_resources` (Schraffuren, Linienarten, Klassen, Ebenen), `vw2d_setup`
(Klassen mit Attributen, Ebenen mit Maßstab, Vorlage „architektur“),
`vw2d_draw` (Polygon, Polylinie, Pfad mit echten Bögen, Rechteck, Kreis, Bogen,
Oval, Linie, Text, Bemaßung, Maßkette, Punkt – mit Rücklese-Abweichung und
Prüfung), `vw2d_edit`, `vw2d_transform` (auch Punkt auf Punkt), `vw2d_boolean`
(vereinigen, abziehen, schneiden, teilen), `vw2d_offset`, `vw2d_set_attributes`,
`vw2d_delete`, `vw2d_check` (mit Prüfbild), `vw2d_preview`, `vw2d_fix` (snap,
close, clean, duplicates, overlaps, merge, by_class), `vw2d_select` (markieren +
zoomen).
**Aus dem Original:** `vw_ping`, `vw_document_info`, `vw_list_layers`,
`vw_list_classes`, `vw_get_selection`, `vw_find_objects`, `vw_get_record`,
`vw_set_record`, `vw_list_symbols`, `vw_insert_symbol`, `vw_revert_last`,
`vw_exec_python`, `vw_vs_lookup`. Spotlight-Werkzeuge nur mit
`VWMCP_SPOTLIGHT=1`.

**Regeln, die Claude mitbekommt** (`app.py`, Skill): exakt (nichts runden,
Maßketten relativ, Bögen echt), lückenlos ohne Überlappung, Zusammengehöriges
als eine Fläche, keine Kanten verlieren, jedes Objekt in eine Klasse (nie
„Keine“), Attribute nach Klasse, vorhandenen Klassenstandard lesen und
folgen, 2D bleibt 2D.

**Genauigkeit:** „identisch“ = 1 nm, gemeldete Dicken ab 0,005 mm,
Fang-Toleranz Standard 1 mm, Bögen auf 1 µm abgetastet; Rechenrauschen von
Vectorworks wird auf die exakten Eingangswerte zurückgesetzt.

## Dateien auf dem Mac (Nutzer)

| Ort | Was |
|---|---|
| `~/.vwmcp/jobs`, `~/.vwmcp/results` | Aufträge an Vectorworks und Antworten |
| `~/.vwmcp/journal.jsonl` | Rückgängig-Journal (`vw_revert_last`) |
| `~/.vwmcp/extension.log` | Log des Starters und der Server-Fehlerausgabe |
| `~/.vwmcp/vwmcp.log` | Log der Brücke in Vectorworks |
| `~/.vwmcp/venv`, `~/.vwmcp/uv` | Python-Umgebung und uv |
| `~/Library/Application Support/Vectorworks/2026/Plug-ins/vwmcp/` | Brücke |
| Claude ▸ Einstellungen ▸ Erweiterungen | installierte Erweiterung |

## Entwickeln, bauen, veröffentlichen

```bash
uv sync
uv run pytest -q                 # 110 Tests
scripts/build-mcpb.sh            # baut release/Keystone-for-Vectorworks.mcpb (braucht node/npx)
                                 # PYTHON=<python mit mcp> scripts/build-mcpb.sh, falls kein uv
```

Neue Version: Version in `src/vwmcp/__init__.py` **und** `pyproject.toml`
erhöhen → bauen → Tests → committen und pushen (die `.mcpb` liegt im Repo,
Installer und Download-Link zeigen auf `main`). Nutzer aktualisieren über den
Installer oder die neue `.mcpb`. Der Build schreibt `server/static.json` aus
dem echten Server – nach jeder Änderung an Werkzeugen neu bauen.

Der vollständige Git-Verlauf liegt im Repo; in diesem Keystone-Ordner
zusätzlich als `Git-Verlauf.bundle` (`git clone Git-Verlauf.bundle vectorworks-2d-mcp`).

## Wichtige Erkenntnisse (nicht nochmal herausfinden müssen)

- **Claude Desktop prüft jede neue Verbindung mit `server/discover`** und
  bricht ab, wenn nach ~3 s keine Antwort kommt („connection closed during
  the server/discover probe“). Der Python-Server startet langsamer → der
  Node-Starter beantwortet Verbindungsaufbau und erste Werkzeugliste selbst
  (wie Keystone for Rhino).
- **Das eingebaute Node von Claude Desktop reicht stdin/stdout nicht an
  Unterprozesse weiter** → Nachrichten werden Zeile für Zeile weitergereicht.
- Der uv-Servertyp der Erweiterungen griff in Claude Desktop nicht → Node-Erweiterung.
- **Claude Desktop schreibt seine Konfiguration beim Beenden zurück** →
  der Installer beendet Claude, bevor er sie ändert.
- Installer ohne git: git verlangte auf dem Mac die Xcode-Lizenz → ZIP-Download.
- `mcp` 2.x (nicht 1.x): `MCPServer`, Icons im `serverInfo`.
- Vectorworks-API-Fallen (Details `docs/vs-notes.md`): Bogenpunkt = Ausrundung,
  `SetCLOpacityN(Klasse, Füllung, Stift)`, Boolesche Operationen erzeugen
  ~1e-15-Rauschen, `OffsetPolyN` legt das Ergebnis auf die aktive Ebene in die
  aktive Klasse, Textausrichtung vor `CreateText` setzen, Menübefehle per
  internem Namen (`Fit To Objects`) sprachunabhängig.
- Umbenennen des GitHub-Repos ist aus Claude-Sitzungen gesperrt (nur über
  GitHub-Einstellungen) – Name bleibt `vectorworks-2d-mcp`.

## Live-Test 29.10.2026 (Vectorworks 2026, „Ohne Titel 3“)

- **Organisation:** Vorlage „architektur“ (19 Klassen, Linienarten importiert),
  Ebene EG 1:50, Klasse mit Schraffur „Beton SRF“ – in Vectorworks korrekt.
- **Zeichnen:** Wände mit Loch, Bogenwand (echter Bogen), Stütze, Möbel,
  Türbogen, Achse, Text, Bemaßung + Maßkette (24 | 50 | 1,01 | 2,14 | 24 =
  4,13 m) – alles mit Abweichung 0.
- **Prüfen/Korrigieren:** 7 eingebaute Fehler (Spalt 0,4 mm, knapp daneben
  0,6 mm, Überlappung, doppelte Stütze, offener Umriss 0,5 mm, überflüssiger
  Punkt, eigene Farbe) – alle gefunden, alle behoben.
- **Bearbeiten:** Türöffnung abgezogen, vereinigt, geteilt, versetzt,
  verschoben (Punkt auf Punkt), gedreht, gespiegelt, skaliert, Klasse/Ebene
  geändert, gelöscht + rückgängig, Zoom – exakt.
- **Gefunden und behoben (0.3.5/0.3.6):** Fangen kippte Wände (jetzt wandert
  die Kante mit), Offset in falscher Klasse/Ebene und mit Rauschen,
  eingeschnürte Vereinigung ohne Warnung, T-Stoß-Knoten als „überflüssig“
  gemeldet und gelöscht, alte UUIDs nach Korrekturen, falscher Aktionsname,
  Prüfbild (Löcher, Textausrichtung). Danach live nachgeprüft.

## Bekannte Grenzen / offene Punkte

- Frische Installation auf einem fremden Mac noch nie gemacht (Installer holt
  seit 31.10. uv selbst – ungetestet).
- Vectorworks 2027 und englische Oberfläche ungetestet.
- Offset von Bögen liefert Radius-Punkte (Typ 4) → nur angenähert geprüft.
- Achsen auf Wandkanten werden als Doppellinie gemeldet → Klasse „Achsen“
  bei der Prüfung auslassen (`ignore_classes`).
- Fangen kann eine Kante nicht mitziehen, wenn deren anderer Punkt an einem
  dritten Objekt hängt (dann bleibt sie minimal schräg – Prüfung zeigt es nicht).
- Vectorworks fasst mehrere Claude-Schritte zu einem Cmd-Z zusammen →
  `vw_revert_last` für einzelne Schritte.
- Offene Dialoge in Vectorworks blockieren Aufträge (Timeout).

## Versionen

| Version | Datum | Inhalt |
|---|---|---|
| 0.2.0 | 29.10.2026 | vw2d-Werkzeuge (exakt zeichnen, prüfen, korrigieren, organisieren), Installer |
| 0.3.0 | 29.10.2026 | Claude-Desktop-Erweiterung „Keystone for Vectorworks“ mit Keystone-Icon |
| 0.3.1–0.3.2 | 29.10.2026 | Node-Starter statt uv-Servertyp, Nachrichten weiterreichen |
| 0.3.3 | 29.10.2026 | Starter beantwortet den Verbindungsaufbau sofort (`static.json`) |
| 0.3.5 | 29.10.2026 | Fixes aus dem Live-Test (Fangen, T-Stöße, Hinweise nach Boolean/Offset/Edit, UUIDs, Prüfbild) |
| 0.3.6 | 29.10.2026 | Offset: richtige Ebene, exakte Koordinaten |
| – | 31.10.2026 | Installer holt uv auf frischen Macs; Projektinfo |
