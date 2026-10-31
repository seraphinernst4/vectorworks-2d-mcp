# Vectorworks-API-Fallstricke (2D)

Welche bekannten Fallen der `vs.*`-API Keystone berücksichtigt, und wie.
Quellen:

- **vwx-mcp** – [`AGENTS.md`](https://github.com/vicquick/vwx-mcp/blob/main/AGENTS.md),
  Abschnitte „VW2026 API gotchas“ und „VW2026 API renames“ (MIT, © 2026 vicquick)
- **vwmcp** – [`docs/reference/api-pitfalls.md`](https://github.com/MattA-Official/vwmcp/blob/main/docs/reference/api-pitfalls.md)
  (MIT, © 2026 Matt Adcock)
- **Keystone** – eigene Live-Tests (`scripts/live_test.py`, `docs/vs-notes.md`)

Übernommen ist nur, was für 2D zählt: Bögen und Kreise, Polygone und
Polylinien, Flächen, Booleans, Klassen und Ebenen, Attribute, Schraffuren.
Wände, 3D, IFC, Arbeitsblätter und Plug-in-Stile bleiben außen vor.

„Live“ heißt: geprüft am 31.10.2026 in Vectorworks 2026 Update 7 (31.7.0,
Build 879357, deutsch, macOS).

## Im Code behoben oder abgefangen

| Falle | Quelle | Live auf 2026 U7 | Keystone |
|---|---|---|---|
| Falsche Argumentzahl (`vs.Rect(0, 0, 1)`) öffnet den modalen Dialog „Skriptfehler“ statt einer Python-Exception | vwmcp, Keystone | bestätigt | **Abgefangen**: Die Brücke ersetzt `vs` pro Job durch einen geprüften Proxy (`vs_index.json`). Der Aufruf kommt gar nicht bei Vectorworks an; das Ergebnis enthält `VSArgumentError` mit der richtigen Signatur. Gilt auch für `vw_exec_python` und `import vs`. |
| `SetFillFore` / `SetPenFore` / `SetFillBack` … mit drei einzelnen Zahlen tun **still nichts** | vwx-mcp, vwmcp | – | **Abgefangen**: Proxy verlangt bei jedem `COLOR`-Parameter ein Tupel `(r, g, b)`. Keystone selbst übergibt immer Tupel. |
| Callback als `lambda *a: …` → Vectorworks zählt benannte Parameter → Skriptfehler-Dialog | vwmcp | – | **Abgefangen**: Proxy lehnt Callbacks mit `*args` ab (`ForEachObject`, `ImportResToCurFileN` …). |
| Text oder UUID statt Handle übergeben | Keystone | – | **Abgefangen**: Proxy meldet `HANDLE got text …; for a UUID use vs.GetObjectByUuid(uuid)`. |
| Namen sind **ein** Namensraum für Objekte, Klassen, Ebenen und Ressourcen: `vs.GetObject("Keine")` liefert die **Klasse** (Typ 94) | vwx-mcp (Ressourcen) | bestätigt | **Behoben**: neue Hilfsfunktion `named(name, types)` in `vwlib`. `vw_insert_symbol` akzeptiert nur echte Symboldefinitionen (Typ 16) und sagt, wenn der Name einer Klasse/Ebene gehört; prüft außerdem, ob wirklich ein Symbol gesetzt wurde (statt still das letzte Objekt zurückzugeben). Objektnamen: verständliche Meldung, *wer* den Namen schon hat. |
| `vs.GetObject` liefert für unbekannte Namen einen Typ-0-Dummy | vwx-mcp, vwmcp | **nicht** reproduziert (liefert nichts) | **Trotzdem abgesichert**: `named()` behandelt Typ 0 als „nicht vorhanden“. |
| `FInGroup` einer **leeren** Gruppe zeigt in die Liste der Ebene | vwx-mcp, vwmcp | nicht reproduziert (liefert nichts) | **Abgesichert**: neue Hilfsfunktion `children()` prüft für jedes Kind, dass sein Parent die Gruppe ist, und ist begrenzt. Ersetzt alle `FInGroup`/`NextObj`-Schleifen (Lesen, Struktur, Snapshot, Booleans). |
| `while h: h = vs.NextObj(h)` kann hängen | vwx-mcp, vwmcp | – | Gruppen-Durchläufe über `children()` (begrenzt); Objektsuche über `ForEachObject` (Keystone sammelt erst und ändert danach). |
| `LNewObj()` liefert nach Operationen, die Objekte ersetzen, das falsche Objekt | vwx-mcp, vwmcp | – | Keystone liest Ergebnisse von Booleans/Offset gezielt aus; `vw_insert_symbol` prüft jetzt Typ und „wirklich neu“. |
| Kreis/Bogen: Radius beim Zurücklesen mit Rauschen (0,6000000000000014 statt 0,6) | Keystone | bestätigt, Ursache gefunden | **Behoben**: Vectorworks speichert exakt; das Rauschen entstand erst in Keystone bei *Startpunkt − Mittelpunkt*. Vollkreise lesen den Radius jetzt aus der gespeicherten Breite (bitgenau), Bögen ohne Rauschen unter 1 Pikometer. Rückleseprüfung meldet für Kreise und Bögen jetzt **0**, und `vw_revert_last` stellt Kreise bitgenau wieder her (vorher wanderte der Mittelpunkt um 2·10⁻¹⁶ m). |

## Geprüft und bewusst nicht übernommen

| Vorschlag | Quelle | Live auf 2026 U7 | Entscheidung |
|---|---|---|---|
| Kreise per `vs.Oval` statt `vs.ArcByCenter`, weil `ArcByCenter` „kaputt“ sei | vwx-mcp, vwmcp | `ArcByCenter(x, y, r, 0, 360)` erzeugt einen Vollkreis (Typ 6), Mittelpunkt und Startpunkt **bitgenau**. `Oval` erzeugt dagegen ein **Oval-Objekt** (Typ 4). | **Bleibt `ArcByCenter`.** Mit `Oval` wäre ein Kreis für Keystone eine Ellipse: Fangen „auf dem Kreis“ und die Bogenlogik der Prüfungen griffen nicht mehr. Vermutliche Ursache des vwx-Befunds: der Aufruf `ArcByCenter((cx, cy), r, 0, 360)` mit Tupel statt fünf Zahlen. |

## Als Hinweis (Werkzeugbeschreibung `vw_exec_python` und Server-Anweisungen)

| Falle | Quelle | Live auf 2026 U7 |
|---|---|---|
| `vs.Arc(p1, p2, start, sweep)`: der letzte Wert ist der **Öffnungswinkel**, nicht der Endwinkel | vwx-mcp, vwmcp | bestätigt: `Arc(…, 30, 90)` → `GetArc` = (30, 90) |
| `GetBBox` eines gedrehten Textes ist ein Bildschirm-Rechteck | vwx-mcp, vwmcp | – (Keystone liest Text über `GetTextOrientation`) |
| Kriterien: Namen in einfachen Anführungszeichen, z. B. `"((R IN ['Part Info']))"`, `"(L='EG')"` | vwx-mcp, vwmcp | – |
| Während `ForEachObject` nichts erzeugen, löschen oder umsortieren – erst sammeln, dann ändern | vwx-mcp, vwmcp | – |
| `ClassList(i)` ist 1-basiert, `ClassNum()` die Anzahl | vwx-mcp | bestätigt: `ClassList(0)` = `""` |
| Umbenannt: `SetClassVisibility` → `ShowClass`/`HideClass`; `ZoomToSel` → `DoMenuTextByName('Fit To Objects', 0)`; `HMirror` → `MirrorN(h, dup, p1, p2, preserveMatrix)`; `SetLName` → `SetName`; `GetClassName`/`GetClName` → `ClassNum` + `ClassList`; `LinDimN` → `LinearDim` (7 Argumente); `SymbolCreate` → `BeginSym` … `EndSym`; `GetVWVersion` → `GetVersionEx` | vwx-mcp, vwmcp | Keystone nutzt durchweg die neuen Formen |
| Alte Strich-/Linienaufrufe → `…N`-Varianten (`SetLSN`) | vwx-mcp, vwmcp | – |
| Schlagschatten: `SetDropShadowData` speichert Versatz/Unschärfe ×1000 | vwx-mcp, vwmcp | – |
| Export/Import (`ImportDWG`, PDF-Export …) öffnen modale Dialoge – nicht unbeaufsichtigt aufrufen | vwx-mcp, vwmcp | – |
| `SetCLOpacityN(Klasse, Füllung, Stift)` – Füllung zuerst | Keystone (`vs-notes.md`) | bestätigt (29.10.) |
| Vectorworks 2027 meldet sich als Hauptversion 32 und bettet Python 3.14 ein | vwmcp, Vectorworks developer-scripting | – (2027 hier nicht installiert) |

## Grundsatz

Der Proxy blockiert nie etwas, das Vectorworks annehmen könnte: Punkte
dürfen als Tupel oder als zwei Zahlen kommen, ein Zahlentupel darf auch
mehrere aufeinanderfolgende Zahlenparameter abdecken, und Funktionen ohne
Indexeintrag oder mit abweichender Live-Signatur gehen ungeprüft durch.
Live auf 2026 U7: Von 3192 Funktionen im Index gibt es 3099 in Vectorworks
2026 (der Rest ist neu in 2027 oder nur VectorScript); 3077 davon werden
geprüft, keine einzige widerspricht Vectorworks' eigenen Docstrings.
