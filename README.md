# Keystone for Vectorworks

<!-- mcp-name: io.github.seraphinernst4/vectorworks-2d-mcp -->

<img src="src/vwmcp/assets/icon.png" width="96" align="right" alt="">

**Claude zeichnet in deiner offenen Vectorworks-Datei – exakt und sauber organisiert.**
Für Architekturpläne in 2D: Flächen schließen lückenlos und ohne Überlappung
aneinander, Bögen sind echte Bögen, jedes Objekt liegt in der richtigen
Klasse und Ebene. Claude prüft seine Arbeit selbst und korrigiert Fehler,
bevor du sie siehst.

> **Beta.** Getestet mit Vectorworks 2026 (Update 7, deutsch) auf macOS.
> Speichere deine Datei, bevor du Claude zeichnen lässt.
> Fork von [giorgiomoroder/vectorworks-mcp](https://github.com/giorgiomoroder/vectorworks-mcp) (MIT),
> dessen Original-README unter [docs/README-upstream.md](docs/README-upstream.md) liegt.

## Was Claude damit kann

| | |
|---|---|
| **Zeichnen** | Polygone, Polylinien mit echten Bögen, Rechtecke, Kreise, Bögen, Linien, Text, Bemaßung und Maßketten. Maße in mm/cm/m, auch als Maßkette (relativ) – exakt gerechnet (3650 mm = 3,65 m, nicht 3,6499999). Nach jedem Zeichnen liest Claude zurück, was Vectorworks gespeichert hat, und meldet die Abweichung (soll: 0). |
| **Prüfen** | Überlappungen und übereinanderliegende Füllflächen, Spalte und Lücken zwischen Nachbarn, Punkte, die knapp danebenliegen, doppelte Flächen und Doppellinien, offene oder sich selbst schneidende Umrisse, überflüssige Punkte, Splitterflächen, Objekte ohne Klasse, Attribute nicht „nach Klasse“. Gemessen an der exakten Geometrie (auch an Bögen), Genauigkeit 0,005 mm. Dazu ein **Prüfbild** mit nummerierten Fehlern. |
| **Korrigieren** | Punkte exakt fangen (Spalte und kleine Überlappungen verschwinden, Bögen bleiben auf ihrem Kreis), Umrisse schließen, Punkte bereinigen, Doppeltes löschen, Überlappungen wegschneiden (das oben liegende Objekt gewinnt – das Bild bleibt gleich), Teile zu einer Fläche vereinigen. |
| **Bearbeiten** | Umriss exakt ersetzen, verschieben (auch Punkt auf Punkt), drehen, spiegeln, skalieren, vereinigen, abziehen, schneiden, teilen, versetzen, Attribute/Klasse/Ebene setzen, löschen. |
| **Organisieren** | Ebenen und Klassen anzeigen und prüfen, Klassen mit Attributen anlegen (optional mit einer Architektur-Vorlage), Ebenen mit Maßstab anlegen. Neue Objekte bekommen ihre Attribute **nach Klasse**. Schraffuren und Linienarten aus dem Vectorworks-Zubehör werden automatisch importiert. |
| **Rückgängig** | `vw_revert_last` macht genau den letzten Schritt von Claude rückgängig (Geometrie, Attribute, Klasse, Name, Stapelreihenfolge). |

So fragst du Claude zum Beispiel:

> - „Zeichne im EG einen 3,65 × 4,80 m Raum mit 24 cm Wänden in Klasse Wand-Tragend.“
> - „Zeichne die Cavea als Ringe mit echten Bögen, jeder Ring eine Fläche.“
> - „Prüf die Ebene Grundriss und behebe alle Fehler.“
> - „Räum die Klassen auf: alles, was in ‚Keine‘ liegt, in passende Klassen.“

## Installation (macOS, ca. 5 Minuten)

Du brauchst **Vectorworks 2026** (2027 geht vermutlich auch, siehe unten) und
die **Claude-Desktop-App**.

### 1. Erweiterung installieren

1. **[Keystone-for-Vectorworks.mcpb herunterladen](https://github.com/seraphinernst4/vectorworks-2d-mcp/raw/main/release/Keystone-for-Vectorworks.mcpb)**
2. Doppelklicken – Claude Desktop öffnet sich und zeigt „Keystone for
   Vectorworks“ – **Installieren** klicken.

Das war's: Python und alle Pakete holt sich die Erweiterung selbst (mit
[uv](https://docs.astral.sh/uv/), nach `~/.vwmcp`), die kleine Brücke in den
Vectorworks-Plug-in-Ordner kopiert sie bei jedem Start selbst (und hält sie
aktuell). Terminal, git oder Xcode brauchst du nicht.

> Der **allererste Start** lädt Python und Pakete (1–2 Minuten). Zeigt Claude
> danach keine Vectorworks-Werkzeuge oder einen Fehler bei der Erweiterung,
> Claude einmal beenden und neu öffnen – ab dann startet sie in 2 Sekunden.
> Log: `~/.vwmcp/extension.log`.

<details>
<summary>Alternativ per Terminal (auch für Claude Code und zum Umstieg von der alten Einrichtung „vectorworks“)</summary>

```bash
curl -LsSf https://raw.githubusercontent.com/seraphinernst4/vectorworks-2d-mcp/main/scripts/install-macos.sh | bash
```

Lädt die Erweiterung, entfernt einen alten Eintrag „vectorworks“ aus der
Claude-Konfiguration (mit Sicherung; Claude Desktop wird dafür kurz beendet),
trägt den Server in Claude Code ein, falls vorhanden, bereitet Python und
Pakete vor (dann ist auch der erste Start schnell) und öffnet die
Erweiterung in Claude Desktop – dort **Installieren** klicken.
</details>

### 2. Menübefehl „MCP Run Job“ anlegen (einmalig, 2 Minuten)

Claude erreicht Vectorworks über einen einzigen Menübefehl. Den kann man nur
im Plug-in-Manager anlegen:

1. **Extras ▸ Plug-ins ▸ Plug-in-Manager…** ▸ Register **Eigene Plug-ins** ▸ **Neu…**
   (englisch: *Tools ▸ Plug-ins ▸ Plug-in Manager… ▸ Custom Plug-ins ▸ New…*)
2. Typ **Befehl**, Name genau **`MCP Run Job`**, **OK**.
3. Den Befehl auswählen ▸ **Skript bearbeiten…** ▸ Sprache **Python**, einfügen:

   ```python
   from vwmcp import vwmcp_bridge
   vwmcp_bridge.run_pending()
   ```

   **OK**, dann **Schließen**.
4. **Extras ▸ Arbeitsumgebungen ▸ Aktuelle Arbeitsumgebung bearbeiten…** ▸
   Register **Menüs**: **MCP Run Job** (links unter *Sonstiges*) in ein Menü
   rechts ziehen, z. B. **Extras**. **OK**.

Test: **Extras ▸ MCP Run Job** wählen – es passiert sichtbar nichts und es
kommt keine Fehlermeldung. Genau so soll es sein.

### 3. Ausprobieren

Mit einer offenen Zeichnung in Vectorworks schreibst du Claude: *„Ping
Vectorworks.“* Beim ersten Mal fragt macOS,
ob Claude Vectorworks steuern darf – **OK** klicken.

**Update:** Neue `.mcpb` herunterladen und doppelklicken. **Vectorworks 2027:**
Claude ▸ Einstellungen ▸ Erweiterungen ▸ Keystone for Vectorworks ▸
Konfigurieren ▸ Vectorworks-Version `2027`. Getestet ist bisher nur 2026.

## Optional: Skill für Claude

Die Zeichenregeln stecken schon im Server. Wer möchte, kann zusätzlich den
Skill [`skill/vectorworks-2d/SKILL.md`](skill/vectorworks-2d/SKILL.md) in
Claude hinzufügen (Einstellungen ▸ Funktionen ▸ Skills). Er beschreibt den
Arbeitsablauf ausführlicher und lässt sich an euren Klassenstandard anpassen.

## So arbeitet Claude damit

1. **Struktur lesen** (`vw2d_structure`): welche Ebenen, Klassen, Maßstäbe gibt es?
2. **Zeichnen** (`vw2d_draw`) in der richtigen Ebene und Klasse – exakt.
3. **Prüfen** (`vw2d_check`) mit Prüfbild.
4. **Korrigieren** (`vw2d_fix`) und erneut prüfen, bis keine Fehler mehr da sind.
5. **Zeigen** (`vw2d_select`): Claude markiert, was es gezeichnet hat.

Alle Werkzeuge: `vw2d_read`, `vw2d_structure`, `vw2d_resources`, `vw2d_setup`,
`vw2d_draw`, `vw2d_edit`, `vw2d_transform`, `vw2d_boolean`, `vw2d_offset`,
`vw2d_set_attributes`, `vw2d_delete`, `vw2d_check`, `vw2d_preview`,
`vw2d_fix`, `vw2d_select`, dazu aus dem Original `vw_ping`,
`vw_document_info`, `vw_list_layers`, `vw_list_classes`, `vw_get_selection`,
`vw_find_objects`, `vw_get_record`, `vw_set_record`, `vw_list_symbols`,
`vw_insert_symbol`, `vw_revert_last`, `vw_exec_python`, `vw_vs_lookup`.
(Die Spotlight-Lichttechnik-Werkzeuge des Originals gibt es mit
`VWMCP_SPOTLIGHT=1`.)

## Gut zu wissen

- **Rückgängig:** Vectorworks fasst mehrere Claude-Schritte zu einem Cmd-Z
  zusammen. Einen einzelnen Schritt von Claude machst du mit „mach das
  rückgängig“ (`vw_revert_last`) rückgängig.
- **Dialoge blockieren:** Ist in Vectorworks ein Dialog offen, wartet Claude
  und meldet dann einen Timeout. Dialog schließen, nochmal fragen.
- **Gerade gestartet?** Claude wartet, bis Vectorworks ~30 Sekunden läuft.
- **Exakt heißt:** Koordinaten werden ungerundet durchgereicht und
  zurückgelesen. Rechenrauschen von Vectorworks' Booleschen Operationen
  (z. B. 26,999999999999996 statt 27) wird automatisch auf die exakten
  Eingangswerte zurückgesetzt.
- **Log:** Fehler der Brücke stehen in `~/.vwmcp/vwmcp.log`.

## Deine Zeichnungsdaten

Was Claude aus deiner Zeichnung liest (Koordinaten, Namen, Klassen, Texte),
geht als Teil deiner Unterhaltung an Anthropic. Die `.vwx`-Datei selbst wird
nicht hochgeladen. Bei Projekten mit Vertraulichkeitsvereinbarung vorher
prüfen, ob das erlaubt ist. Die Brücke läuft komplett auf deinem Mac
(`~/.vwmcp/`).

## Fehlersuche

| Problem | Lösung |
|---|---|
| „did not pick up the job“ | Zeichnung öffnen, Dialoge schließen, prüfen, ob **MCP Run Job** in einem Menü liegt (Schritt 2.4). Einmal von Hand ausführen – es darf nichts passieren. |
| „is not running“ / „still starting“ | Vectorworks öffnen und ein paar Sekunden warten. |
| Fenster „Skriptfehler“ in Vectorworks | OK klicken und Claude bitten, es anders zu versuchen. |
| `vw_ping` warnt wegen der Brücken-Version | Vectorworks neu starten (die Erweiterung hat die Brücke aktualisiert). |
| Werkzeuge doppelt („vectorworks“ und „Keystone for Vectorworks“) | Terminal-Installer einmal ausführen – er entfernt den alten Eintrag. |
| Keine macOS-Nachfrage, nichts passiert | Systemeinstellungen ▸ Datenschutz & Sicherheit ▸ Automation: Claude darf Vectorworks steuern. |

## Für Entwickler

```bash
uv sync
uv run pytest -q          # Server-Logik; Code, der in Vectorworks läuft, wird live getestet
uv run vwmcp              # Server über stdio
scripts/build-mcpb.sh     # baut release/Keystone-for-Vectorworks.mcpb (braucht node)
```

Die Erweiterung (`mcpb/`: Manifest, Abhängigkeiten, Startdateien) ist eine
Node-Erweiterung: `server/index.js` läuft mit dem Node von Claude Desktop,
sucht uv (oder lädt es einmal nach `~/.vwmcp/uv`) und startet damit
`server/main.py` (Python und Pakete in `~/.vwmcp/venv`). Der Server-Code
liegt als Quelltext bei. Version: `src/vwmcp/__init__.py`.

Aufbau: `vw-plugin/` ist die Brücke in Vectorworks (unverändert vom
Original), `src/vwmcp/vwlib2d.py` läuft *in* Vectorworks (Python 3.9!),
`src/vwmcp/geom/` rechnet auf dem Server (exakte Bögen, Prüfungen,
Korrekturen, Prüfbild), `src/vwmcp/tools/draw2d.py` sind die MCP-Werkzeuge.
Details in [CLAUDE.md](CLAUDE.md), [docs/architecture.md](docs/architecture.md)
und [docs/vs-notes.md](docs/vs-notes.md).

## Lizenz

MIT – © giorgiomoroder (Original) und Seraphin Ernst (2D-Werkzeuge).
