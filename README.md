# Keystone for Vectorworks

<!-- mcp-name: io.github.seraphinernst4/vectorworks-2d-mcp -->

<img src="src/vwmcp/assets/icon.png" width="96" align="right" alt="">

**Claude zeichnet in deiner offenen Vectorworks-Datei – exakt und sauber organisiert.**
Für Architekturpläne in 2D: Flächen schließen lückenlos und ohne Überlappung
aneinander, Bögen sind echte Bögen, jedes Objekt liegt in der richtigen
Klasse und Ebene. Claude prüft seine Arbeit selbst und korrigiert Fehler,
bevor du sie siehst.

> **Beta.** Getestet mit Vectorworks 2026 (Update 7, deutsch) auf macOS;
> Vectorworks 2027 ist vorbereitet (Python 3.14, neue Funktionen im
> Signatur-Index), aber noch nicht live getestet.
> Speichere deine Datei, bevor du Claude zeichnen lässt.
> Fork von [giorgiomoroder/vectorworks-mcp](https://github.com/giorgiomoroder/vectorworks-mcp) (MIT),
> dessen Original-README unter [docs/README-upstream.md](docs/README-upstream.md) liegt.

## Was Claude damit kann

| | |
|---|---|
| **Zeichnen** | Polygone, Polylinien mit echten Bögen, Rechtecke, Kreise, Bögen, Linien, Text, Bemaßung und Maßketten. Maße in mm/cm/m, auch als Maßkette (relativ) – exakt gerechnet (3650 mm = 3,65 m, nicht 3,6499999). Nach jedem Zeichnen liest Claude zurück, was Vectorworks gespeichert hat, und meldet die Abweichung (soll: 0). |
| **Prüfen** | Überlappungen und übereinanderliegende Füllflächen, Spalte und Lücken zwischen Nachbarn, Punkte, die knapp danebenliegen, doppelte Flächen und Doppellinien, offene oder sich selbst schneidende Umrisse, überflüssige Punkte, Splitterflächen, Objekte ohne Klasse, Attribute nicht „nach Klasse“. Gemessen an der exakten Geometrie (auch an Bögen), Genauigkeit 0,005 mm. Dazu ein **Prüfbild** mit nummerierten Fehlern. |
| **Korrigieren** | Punkte exakt fangen (Spalte und kleine Überlappungen verschwinden, Bögen bleiben auf ihrem Kreis, Kanten behalten ihre Richtung – Wände bleiben rechtwinklig), Umrisse schließen, Punkte bereinigen (Knotenpunkte an T-Stößen bleiben), Doppeltes löschen, Überlappungen wegschneiden (das oben liegende Objekt gewinnt – das Bild bleibt gleich), Teile zu einer Fläche vereinigen. |
| **Bearbeiten** | Umriss exakt ersetzen, verschieben (auch Punkt auf Punkt), drehen, spiegeln, skalieren, vereinigen, abziehen, schneiden, teilen, versetzen, Attribute/Klasse/Ebene setzen, löschen. |
| **Organisieren** | Ebenen und Klassen anzeigen und prüfen, Klassen mit Attributen anlegen (optional mit einer Architektur-Vorlage), Ebenen mit Maßstab anlegen. Neue Objekte bekommen ihre Attribute **nach Klasse**. Schraffuren und Linienarten aus dem Vectorworks-Zubehör werden automatisch importiert. |
| **Sortieren** | Ganze Zeichnungen auf Klassen und Ebenen verteilen – nach Regeln (Klasse/Ebene als Muster wie `A-WALL*`, Objektart, Text, Stiftfarbe, Strichstärke, Fläche, Größe …), z. B. nach einem DWG-Import. Erst eine Probe („was würde sich ändern, was bleibt übrig?“), dann alles in **einem** Schritt, der sich als Ganzes zurücknehmen lässt. |
| **Assets** | Exakt gezeichnete Symbole für Pläne: WC (wandhängend/stehend), Urinal, Waschtisch (auch doppelt), Handwaschbecken, Dusche, Badewanne, Waschmaschine, Küchenzeile, Spüle, Kochfeld, Kühlschrank, Stuhl, Esstisch mit Stühlen (eckig/rund), Bett, Sofa, Sessel, Couchtisch, Nachttisch, Kommode, Regal, Schrank, Schreibtisch, Laub- und Nadelbaum, Strauch, Person, PKW, Fahrrad – im **Grundriss** und, wo sinnvoll, in **Ansicht** und **Seitenansicht/Schnitt**. Maße änderbar (z. B. WC-Höhe 460 für barrierefrei), gespiegelt, gedreht, mehrfach in einem Aufruf. Jede Variante wird **ein** Vectorworks-Symbol (Ordner „Keystone …“ im Zubehör-Manager), Linien in Klasse und Detail-Klasse, alles nach Klasse. |
| **Rückgängig** | `vw_revert_last` macht genau den letzten Schritt von Claude rückgängig (Geometrie, Attribute, Klasse, Name, Stapelreihenfolge). |

So fragst du Claude zum Beispiel:

> - „Zeichne im EG einen 3,65 × 4,80 m Raum mit 24 cm Wänden in Klasse Wand-Tragend.“
> - „Zeichne die Cavea als Ringe mit echten Bögen, jeder Ring eine Fläche.“
> - „Prüf die Ebene Grundriss und behebe alle Fehler.“
> - „Räum die Klassen auf: alles, was in ‚Keine‘ liegt, in passende Klassen.“
> - „Sortier den DWG-Import: A-WALL* nach Wand-Tragend, Texte nach Beschriftung, Bemaßungen nach Bemaßung.“
> - „Setz in das Bad ein wandhängendes WC (barrierefrei, 46 cm), einen Waschtisch 80 cm und eine bodengleiche Dusche 120 × 120.“
> - „Zeichne vor die Fassade drei Bäume und zwei Personen in der Ansicht.“

## Installation (macOS, ca. 5 Minuten)

Du brauchst **Vectorworks 2026 oder 2027** und die **Claude-Desktop-App**.

### 1. Erweiterung installieren

1. **[Keystone-for-Vectorworks.mcpb herunterladen](https://github.com/seraphinernst4/vectorworks-2d-mcp/raw/main/release/Keystone-for-Vectorworks.mcpb)**
2. Doppelklicken – Claude Desktop öffnet sich und zeigt „Keystone for
   Vectorworks“ – **Installieren** klicken.

Das war's: Python und alle Pakete holt sich die Erweiterung selbst (mit
[uv](https://docs.astral.sh/uv/), nach `~/.vwmcp`), die kleine Brücke kopiert
sie bei jedem Start in den Plug-in-Ordner **jeder** Vectorworks-Version ab
2026 auf deinem Mac (und hält sie aktuell – ein laufendes Vectorworks lädt
die neue Brücke selbst nach). Terminal, git oder Xcode brauchst du nicht.

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
im Plug-in-Manager anlegen. **Vorher Vectorworks einmal neu starten**, damit
es die Brücke findet, die die Erweiterung gerade in den Plug-in-Ordner gelegt
hat.

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
nichts einstellen – Keystone spricht mit dem Vectorworks, das gerade läuft.
Nur wenn 2026 und 2027 gleichzeitig offen sind, entscheidet die Einstellung
Claude ▸ Einstellungen ▸ Erweiterungen ▸ Keystone for Vectorworks ▸
Konfigurieren ▸ Vectorworks-Version (z. B. `2027`). Den Menübefehl „MCP Run
Job“ übernimmt Vectorworks 2027, wenn du beim ersten Start die Einstellungen
und Plug-ins aus 2026 migrierst; sonst einmal wie oben anlegen.

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
`vw2d_fix`, `vw2d_select`, `vw2d_organize`, `vw2d_assets`,
`vw2d_place_asset`, dazu aus dem Original `vw_ping`,
`vw_document_info`, `vw_list_layers`, `vw_list_classes`, `vw_get_selection`,
`vw_find_objects`, `vw_get_record`, `vw_set_record`, `vw_list_symbols`,
`vw_insert_symbol`, `vw_revert_last`, `vw_exec_python`, `vw_vs_lookup`.
(Die Spotlight-Lichttechnik-Werkzeuge des Originals gibt es mit
`VWMCP_SPOTLIGHT=1`.)

## Gut zu wissen

- **Rückgängig:** Vectorworks fasst mehrere Claude-Schritte zu einem Cmd-Z
  zusammen. Einen einzelnen Schritt von Claude machst du mit „mach das
  rückgängig“ (`vw_revert_last`) rückgängig.
- **Offene Dialoge:** Nimmt Vectorworks einen Auftrag nicht innerhalb von
  15 Sekunden an (meist, weil ein Dialog offen ist), bricht Claude ihn ab –
  und er läuft garantiert **nicht** später nach, wenn du den Dialog
  schließt. Dialog schließen, nochmal fragen.
- **Lange Aufträge** (z. B. eine Prüfung großer Pläne): Hat Vectorworks einen
  Auftrag angenommen, wartet Claude bis zu 10 Minuten auf das Ergebnis.
  Gemessen mit 10 000 Objekten: prüfen 2,4 s, Duplikate löschen 5 s,
  Überlappungen wegschneiden 5 s, sortieren 1,2 s, jeweils rückgängig ~1 s.
- **Keine Skriptfehler-Fenster mehr durch falsche Aufrufe:** Jeder `vs.*`-Aufruf
  wird vorher gegen die Signaturen aller ~3200 Vectorworks-Funktionen geprüft
  (Anzahl und Art der Argumente, Farben als Tupel, Callbacks). Statt des
  modalen Fensters „Skriptfehler“ bekommt Claude eine klare Meldung mit der
  richtigen Signatur und korrigiert sich selbst.
- **Gerade gestartet?** In den ersten ~20 Sekunden nach dem Start von Vectorworks lehnt Keystone
  Aufträge ab („still starting“) – Skripte während des Starts bringen Vectorworks 2026 zum
  Absturz. Kurz warten, dann nochmal fragen.
- **Vectorworks abgestürzt oder beendet?** Merkt Keystone innerhalb von ~2 Sekunden und meldet
  es, statt bis zu 10 Minuten auf ein Ergebnis zu warten.
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
| „did not take the request within 15s“ | Zeichnung öffnen, Dialoge schließen, prüfen, ob **MCP Run Job** in einem Menü liegt (Schritt 2.4). Einmal von Hand ausführen – es darf nichts passieren. Der abgebrochene Auftrag läuft nicht nach. |
| „VSArgumentError: vs.… not called“ | Kein Problem: ein falscher Aufruf wurde abgefangen, bevor er Vectorworks erreicht hat. Claude korrigiert ihn. |
| „is not running“ / „still starting“ | Vectorworks öffnen und ein paar Sekunden warten. |
| Fenster „Skriptfehler“ in Vectorworks | Selten geworden (falsche Argumente fängt Keystone ab). OK klicken und Claude bitten, es anders zu versuchen. |
| `vw_ping` warnt wegen der Brücken-Version | Nochmal pingen (Keystone lädt die neue Brücke selbst nach); hilft das nicht, Vectorworks neu starten. |
| Werkzeuge doppelt („vectorworks“ und „Keystone for Vectorworks“) | Terminal-Installer einmal ausführen – er entfernt den alten Eintrag. |
| Keine macOS-Nachfrage, nichts passiert | Systemeinstellungen ▸ Datenschutz & Sicherheit ▸ Automation: Claude darf Vectorworks steuern. |

## Für Entwickler

```bash
uv sync
uv run pytest -q          # Server-Logik; Code, der in Vectorworks läuft, wird live getestet
uv run vwmcp              # Server über stdio
scripts/build-mcpb.sh     # baut release/Keystone-for-Vectorworks.mcpb (braucht node)
python scripts/live_test.py        # Live-Test gegen das laufende Vectorworks (Protokoll,
                                   # Signaturprüfung, Fallstricke, Regression)
python scripts/trigger_bench.py    # misst die AppleScript-Auslösung
python tools/build_vs_index.py <vs.py> --vwx <vwx vs_index.json> \
    --versions "<Functions By Version.md>" --reference "<Function Reference/Functions>"
                                   # Signatur-Index neu bauen (neues SDK)
```

Die Erweiterung (`mcpb/`: Manifest, Abhängigkeiten, Startdateien) ist eine
Node-Erweiterung: `server/index.js` läuft mit dem Node von Claude Desktop,
sucht uv (oder lädt es einmal nach `~/.vwmcp/uv`) und startet damit
`server/main.py` (Python und Pakete in `~/.vwmcp/venv`). Den Verbindungsaufbau
(`server/discover`, `initialize`, erste `tools/list`) beantwortet der Starter
sofort aus `server/static.json`, das der Build aus dem echten Server erzeugt –
Claude bricht Verbindungen ab, die darauf nicht in ~3 s antworten. Der
Server-Code liegt als Quelltext bei. Version: `src/vwmcp/__init__.py`.

Aufbau: `vw-plugin/` ist die Brücke in Vectorworks (atomares Job-Protokoll,
geprüfter `vs`-Proxy, Bibliotheks-Cache; Python 3.9 **und** 3.14),
`src/vwmcp/data/vs_index.json` der Signatur-Index,
`src/vwmcp/vwlib2d.py` läuft *in* Vectorworks (Python 3.9!),
`src/vwmcp/geom/` rechnet auf dem Server (exakte Bögen, Prüfungen,
Korrekturen, Prüfbild), `src/vwmcp/tools/draw2d.py` sind die MCP-Werkzeuge.
Details in [CLAUDE.md](CLAUDE.md), [docs/architecture.md](docs/architecture.md),
[docs/api-pitfalls.md](docs/api-pitfalls.md) und [docs/vs-notes.md](docs/vs-notes.md).
Änderungen: [CHANGELOG.md](CHANGELOG.md).

## Lizenz

MIT – © giorgiomoroder (Original) und Seraphin Ernst (2D-Werkzeuge).
Enthält Material aus [vwx-mcp](https://github.com/vicquick/vwx-mcp) (MIT, © vicquick:
Beschreibungen im Signatur-Index, Index-Builder, API-Fallstricke) und folgt
dem Job-Protokoll von [vwmcp](https://github.com/MattA-Official/vwmcp)
(MIT, © Matt Adcock; Fallstricke-Liste). Signaturen und Parametertypen aus
Vectorworks' [developer-scripting](https://github.com/Vectorworks/developer-scripting).
Details in [NOTICE](NOTICE).
