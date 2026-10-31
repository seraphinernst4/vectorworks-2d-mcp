# Changelog

## 0.5.0 – Vorschau (Zweig v0.5)

Assets und Sortieren.

### Asset-Katalog (`vw2d_assets`, `vw2d_place_asset`)
- 28 Assets (43 Ansichten), exakt aus Code gezeichnet (mm, echte Bögen, Korbbögen statt
  angenäherter Ellipsen): Sanitär (WC wandhängend/stehend, Urinal,
  Waschtisch einfach/doppelt, Handwaschbecken, Dusche mit Rinne oder
  Punktablauf, Badewanne, Waschmaschine), Küche (Küchenzeile mit Spüle und
  Kochfeld, Spüle, Kochfeld, Kühlschrank), Möbel (Stuhl, Esstisch mit
  Stühlen eckig/rund, Bett, Sofa, Sessel, Couchtisch, Nachttisch, Kommode,
  Regal, Schrank, Schreibtisch), Staffage (Laubbaum, Nadelbaum, Strauch,
  Person, PKW, Fahrrad) – im Grundriss und, wo sinnvoll, in Ansicht und
  Seitenansicht.
- Maße als Parameter (z. B. WC-Höhe 460 barrierefrei, Dusche 1200 × 1200),
  Vorschaubild ohne Vectorworks.
- In Vectorworks wird jede Variante **ein Symbol** (Ordner „Keystone
  Sanitär/Küche/Möbel/…“ im Zubehör-Manager, Beschreibung mit Maßen und
  Einsetzpunkt), Linien in Klasse „Ausstattung-…“/„Staffage-…“ und
  „…-Detail“, alle Attribute nach Klasse; vorhandene Klassen bleiben
  unverändert, ein vorhandenes Symbol wird wiederverwendet (eigene
  Änderungen bleiben).
- Gespiegelte Varianten sind eigene, exakt gespiegelte Symbole
  (`vs.Mirror` auf Symbolinstanzen ist in Vectorworks 2026 kaputt).
- Mehrere Instanzen in einem Aufruf; als Gruppe statt Symbol möglich.
- Rückleseprüfung der Symboldefinition gegen den Katalog; `vw_revert_last`
  entfernt Instanzen und – wenn dieser Aufruf sie angelegt hat und nichts
  anderes sie nutzt – Symbol und Ordner.
- Live (VW 2026 U7): alle Ansichten exakt (Abweichung ≤ 5·10⁻¹³ mm), ~0,5 s je Asset.

### Sortieren (`vw2d_organize`)
- Regeln „wenn … dann Klasse/Ebene/nach Klasse“: Klasse, Ebene, Name,
  Symbol (Muster wie `A-WALL*`), Objektart, Text (regulärer Ausdruck),
  Stift-/Füllfarbe, Füllung, Schraffur, Linienart, Strichstärke, Fläche,
  Größe, geschlossen, in Gruppe, `not`. Kurzformen `class_map`,
  `layer_map`.
- Probelauf zuerst: was jede Regel ändert, was übrig bleibt (gruppiert nach
  Art/Klasse/Ebene/Stift/Strichstärke). Anwenden in **einem** Schritt,
  als Ganzes rücknehmbar; bricht etwas ab, wird alles zurückgesetzt.
- Live: 10 000 Objekte in ~6 s sortiert, in ~3,6 s zurückgenommen.

### Große Zeichnungen
- `vw2d_check` liest bis zu 100 000 Objekte und sagt, wenn es mehr gibt.
- Live gemessen (10 000 Objekte): lesen 0,6 s, prüfen 2,4 s, Duplikate
  löschen 12 s, Überlappungen wegschneiden 16 s.

## 0.4.0 – 31.10.2026

Stabiler, schneller, bereit für Vectorworks 2027.

### Job-Protokoll (atomar)
- Der Server schreibt `<id>.tmp` und benennt erst dann in `<id>.json` um.
- Die Brücke beansprucht einen Job durch Umbenennen in `<id>.working`; nur
  wer das schafft, führt aus. Ergebnis über `.tmp` → `.json`, danach wird
  `.working` gelöscht.
- Zwei Fristen statt einer: Nimmt Vectorworks den Auftrag nicht in **15 s**
  an (Dialog offen, beschäftigt), benennt der Server ihn in `.cancelled` um –
  dann läuft er nie mehr. Scheitert das Umbenennen, hat Vectorworks ihn
  gerade beansprucht, und der Server wartet auf das Ergebnis.
  Angenommene Aufträge werden bis zu **10 min** abgewartet (vorher brach
  alles nach 30 s ab, das Ergebnis blieb liegen und der nächste Aufruf
  meldete fälschlich „Dialog offen“).
- Kein Abweisen mehr, wenn ein AppleScript noch wartet; höchstens 3
  gleichzeitig, verlorene Auslöser werden nach 2 s erneut gesendet.
- Aufräumen: alte Ergebnisse, abgebrochene und verwaiste Jobs (> 1 h).
- Live getestet: Vectorworks blockiert, zweiter Auftrag wird abgebrochen und
  läuft auch nach dem Freiwerden nicht nach; 18-s-Auftrag wird abgewartet.
- Stürzt Vectorworks ab oder wird beendet, während es einen Auftrag
  bearbeitet, meldet Keystone das nach ~2 s (statt bis zu 10 min zu warten).
- Scheitert ein Auslöser, nachdem ein früherer den Auftrag schon
  übernommen hat, wartet der Server auf das Ergebnis statt einen Fehler zu
  melden (der Auftrag läuft ja). Die Annahmefrist zählt auch die Uhrzeit,
  damit ein Ruhezustand des Macs sie nicht verlängert.

### Signatur-Index und Argumentprüfung
- `src/vwmcp/data/vs_index.json`: alle 3192 `vs.*`-Funktionen mit
  Parametertypen, Rückgabe, Kategorie und Version – inkl. der 51 neuen
  Funktionen aus Vectorworks 2027. Gebaut mit `tools/build_vs_index.py`
  (nach vwx-mcp) aus Vectorworks' Stub und Funktionsreferenz.
- Die Brücke ersetzt `vs` pro Job durch einen geprüften Proxy (auch für
  `vw_exec_python` und `import vs`): falsche Argumentzahl, Text statt Zahl
  oder Handle, Farbe als lose Zahlen, `lambda *a`-Callbacks → Python-
  Exception `VSArgumentError` mit der richtigen Signatur statt des modalen
  Skriptfehler-Dialogs. Was Vectorworks annehmen könnte, geht durch: geprüft
  wird nur, was Vectorworks' eigener Docstring bestätigt; `vs`-Funktionen als
  Callback (`vs.ForEachObject(vs.SetSelect, …)`) werden unverändert
  weitergereicht; 17 im Stub falsch typisierte Funktionen bleiben ungeprüft.
  Live: 3077 Funktionen geprüft, 0 Widersprüche zu Vectorworks' Docstrings.
- `vw_vs_lookup` antwortet aus dem Index (sofort, ohne Vectorworks), mit
  Typen und „ab Version“; nur ohne Treffer fragt es live in Vectorworks.

### API-Fallstricke (docs/api-pitfalls.md)
- Kreise/Bögen: Rückleseprüfung jetzt **exakt 0** (das Rauschen entstand in
  Keystones Radius-Berechnung, nicht in Vectorworks); `vw_revert_last`
  stellt Kreise bitgenau wieder her.
- Namen sind ein Namensraum für Objekte, Klassen, Ebenen, Ressourcen:
  `vw_insert_symbol` nimmt nur echte Symbole und prüft das gesetzte Objekt
  (auch Symbole, die beim Einsetzen zur Gruppe oder zum Plug-in-Objekt
  werden).
- Bogenradien: nur echtes Rechenrauschen wird geglättet; ein Radius wie
  1000/3 mm bleibt unverändert (vorher auf 1 Pikometer gerundet).
- Gruppen werden sicher durchlaufen (leere Gruppen, begrenzte Schleifen).
- Kreise bleiben `ArcByCenter` (live exakt; `Oval` würde Ellipsen-Objekte
  erzeugen) – begründet in der Doku.
- Hinweise in `vw_exec_python` und den Server-Anweisungen.

### Vectorworks 2027 und Installation
- Keystone spricht mit dem Vectorworks, das läuft (2026 oder 2027); die
  Versions-Einstellung ist nur noch eine Vorliebe, falls mehrere laufen.
- Die Brücke und der Index werden in jede Vectorworks-Version ab 2026
  installiert – auch wenn in den Einstellungen eine Version fest gewählt
  ist; ein laufendes Vectorworks lädt eine neue Brücke selbst nach
  (`vw_ping` stößt das bei einer alten Brücke auch selbst an).
- Brücke und Bibliotheken laufen unter Python 3.9 (VW 2026) und 3.14
  (VW 2027) – getestet mit beiden Interpretern.
- Vectorworks wird per `ps` erkannt (BSD-`pgrep` übersah es, wenn der
  Aufruf aus Vectorworks heraus kam).

### Tempo
- vwlib/vwlib2d werden nicht mehr bei jedem Aufruf als 60 KB Quelltext
  geschickt und kompiliert, sondern einmal je Vectorworks-Sitzung
  (10,5 ms → 0,7 ms in Vectorworks).
- Ein Aufruf dauert ~0,2 s; davon < 1 ms in Vectorworks, der Rest ist das
  Starten des Menübefehls (gemessen mit `scripts/trigger_bench.py`).

### Werkzeuge für Entwickler
- `scripts/live_test.py` (Protokoll, Signaturprüfung, Fallstricke,
  Regression gegen das laufende Vectorworks), `scripts/trigger_bench.py`.

## 0.3.6 – 29.10.2026
Offset: richtige Ebene, exakte Koordinaten.

## 0.3.5 – 29.10.2026
Fixes aus dem Live-Test (Fangen, T-Stöße, Hinweise nach Boolean/Offset/Edit, UUIDs, Prüfbild).

## 0.3.3 – 29.10.2026
Starter beantwortet den Verbindungsaufbau sofort (`static.json`).

## 0.3.1–0.3.2 – 29.10.2026
Node-Starter statt uv-Servertyp, Nachrichten weiterreichen.

## 0.3.0 – 29.10.2026
Claude-Desktop-Erweiterung „Keystone for Vectorworks“ mit Keystone-Icon.

## 0.2.0 – 29.10.2026
vw2d-Werkzeuge (exakt zeichnen, prüfen, korrigieren, organisieren), Installer.
