---
name: vectorworks-2d
description: Exakte, sauber organisierte 2D-Architekturzeichnungen in Vectorworks mit den vw2d-Werkzeugen (Keystone for Vectorworks). Verwenden, wenn in einer offenen Vectorworks-Datei gezeichnet, geprüft, bereinigt oder nach Ebenen/Klassen organisiert werden soll.
---

# Exakt zeichnen in Vectorworks (2D)

Ziel: Pläne, die ein Architekturbüro so abgeben würde – so, wie **dieser
Nutzer** sie haben will.

## Der Nutzer entscheidet

- Was der Nutzer im Chat ausdrücklich verlangt, gilt immer vor jeder Regel
  hier: Bittet er ums Vereinfachen, wird vereinfacht; will er alles
  behalten, bleibt alles.
- Seine Arbeitsweise steht in seinem **Profil** (`vw2d_profile`):
  Detailtreue (`detail`), Teile vereinigen (`merge_parts`), Flächen
  (`areas`), Bögen (`curves`), Klassen, Attribute, Prüfmodus
  (`check_mode`), ab wie vielen Objekten erst nachgefragt wird
  (`confirm_above`), Toleranzen und **eigene Regeln** in seinen Worten.
- Korrigiert der Nutzer, wie gearbeitet wird, und soll es künftig so
  bleiben: anbieten, es im Profil zu speichern (Einstellung oder eigene
  Regel). Nie ungefragt ändern.
- Unklar bei etwas, das viel Geometrie verändert: einmal nachfragen.

## Grundregeln (Voreinstellungen – das Profil kann sie ändern)

1. **Exakt.** Koordinaten in Dokumenteinheiten oder mit `unit="mm"/"m"`.
   Nichts schätzen, nichts runden. Maßketten als `relative: true`.
   Bögen als echte Bögen (`path` mit `arc_to`). Nach jedem Zeichnen
   `all_exact` prüfen – Abweichung muss 0 sein.
2. **Lückenlos und ohne Überlappung** (`areas`). Benachbarte Flächen teilen
   sich exakt ihre Kanten. Zusammengehörige Bauteile zu einer Fläche
   vereinigen – nach `merge_parts` (Standard: vorschlagen).
3. **Detailtreue** (`detail`). Beim Nachzeichnen jede Kante und Stufe
   übernehmen; vereinfachen, wenn der Nutzer darum bittet.
4. **Organisiert.**
   - Konstruktionsebenen trennen Geschosse/Zeichnungsbereiche (z. B. „EG“,
     „OG1“, „Lageplan“) und haben den richtigen Maßstab.
   - Klassen trennen Bauteile (Wände, Stützen, Öffnungen, Treppen,
     Ausstattung, Achsen, Bemaßung, Beschriftung, Schraffur …). Hierarchie
     mit Bindestrich („Wand-Tragend“).
   - Jedes Objekt in eine passende Klasse; grafische Attribute nach Klasse
     (`classes`, `attributes`).
   - Erst `vw2d_structure` lesen und dem vorhandenen Standard folgen.
     Fehlendes mit `vw2d_setup` anlegen (die Vorlage „architektur“ nur, wenn
     es noch keinen Standard gibt und der Nutzer zustimmt).
5. **2D bleibt 2D.** In der geöffneten Datei arbeiten, nichts in andere
   Dateien oder Programme übertragen.

## Ablauf (neue Zeichnung)

1. `vw2d_structure` – Ebenen, Klassen, Probleme.
2. Plan klären: Welche Ebene, welche Klassen, welche Maße? Bei Unklarheit
   kurz nachfragen.
3. `vw2d_draw` (mehrere Objekte in einem Aufruf, Klasse immer angeben).
4. `vw2d_check` auf der Ebene (`tolerance_mm` aus dem Profil; Klassen, die
   absichtlich überlappen – Möbel, Schraffuren – über `ignore_classes`).
5. Fehler mit `vw2d_fix` beheben (`snap`, `overlaps`, `close`, `clean`,
   `duplicates`, `merge`, `by_class`) und erneut prüfen – so weit der
   Prüfmodus des Profils es vorsieht.
6. Ergebnis zeigen: `vw2d_preview` (mit `marks=false` die Zeichnung ohne
   Markierungen) und `vw2d_select`.
7. Kurz berichten: was gezeichnet wurde, in welchen Ebenen/Klassen.

## Importe, Schnitte, Ansichten bereinigen (Rhino Make2D, VectorView, DWG)

1. **Aufnehmen:** `vw2d_import(path=…)` importiert DWG/DXF ohne Dialog,
   `vw2d_import(layer=…)` nimmt eine schon importierte Ebene auf. Die
   Antwort sagt, was angekommen ist, was fehlt, wie zerstückelt die
   Zeichnung ist, und was als Nächstes sinnvoll ist. Fehlende Objekte dem
   Nutzer sagen: Vectorworks übernimmt keine gewichteten Splines (Kreise,
   Ellipsen, Ringe aus Rhino); R12-DXF aus Rhino enthält gar keine Splines –
   in Rhino als DWG 2018 mit „Splines und Polykurven als Polylinien“ neu
   exportieren.
2. **Prüfen:** `vw2d_check(preset="drawing")` – lose Stücke, Fugen zwischen
   Make2D-Stücken, Umrisse auf Füllflächen und Umrisse in Umrissen (Fenster
   in der Fassade) sind bei solchen Zeichnungen normal und kein Fehler.
   Nicht „bis fehlerfrei“ reparieren.
3. **Sortieren und Linienstärken:** `vw2d_organize` (auch nach Bereich:
   `bbox`/`touches`, z. B. ein Vorhang), dann Attribute nach Klasse und
   Linienstärken der Klassen mit `vw2d_setup`.
4. **Verbinden:** `vw2d_join` macht aus losen Stücken Linienzüge
   (geschlossene ohne Füllung).
5. **Vereinfachen:** `vw2d_simplify` dünnt Punkte aus (Toleranz aus dem
   Profil: Papier-mm × Maßstab), erst Vorschau rot/schwarz zeigen, dann
   `dry_run=false`. Organisches bleibt organisch – nur weniger Punkte;
   gemeinsame Kanten von Nachbarflächen bleiben fugenlos.
6. **Gruppieren:** `vw2d_group` (nach Bereich, Klasse oder Auswahl; die
   Gruppe bekommt die Klasse ihrer Objekte, `group_class` wählt eine andere).
7. Große Zeichnungen Bereich für Bereich; über `confirm_above` Objekten erst
   an einem Ausschnitt zeigen und auf das OK warten. Kein Massenbearbeiten
   mit `vw_exec_python` – fehlt ein Werkzeug, das sagen.

## Tipps

- Wandstärken: Umriss zeichnen und `vw2d_offset` nutzen, oder Wände als
  geschlossene Polygone mit exakten Maßen.
- Öffnungen in Flächen: `holes` beim Zeichnen oder `vw2d_boolean` subtract.
- Punkt exakt auf Punkt verschieben: `vw2d_transform` mit `from_point` und
  `to_point`.
- Rückgängig für genau einen Schritt: `vw_revert_last` (auch nach Import,
  Verbinden, Vereinfachen, Gruppieren).

## Sortieren (DWG-Import, alles in „Keine“)

1. `vw2d_structure` lesen: welche Klassen/Ebenen gibt es, was ist der
   Bürostandard?
2. `vw2d_organize` als Probelauf (Standard): Regeln wie
   `{"match": {"class": "A-WALL*"}, "class": "Wand-Tragend", "by_class": true}`,
   Texte über `kind`/`text`, Bemaßungen über `kind: "dimension"`,
   Strichstärken über `line_weight_mm`. Die Antwort zeigt, was übrig bleibt
   (gruppiert nach Art/Klasse/Ebene/Stift/Strichstärke) – Regeln ergänzen.
3. Dem Nutzer kurz zeigen, was sich ändert; dann `dry_run=false`. Ein
   Schritt, als Ganzes mit `vw_revert_last` rücknehmbar.
4. Danach `vw2d_check` und `vw2d_fix duplicates`/`snap` für Doppeltes und
   Unsauberes.

## Planen mit dem Raumpilot-Wissen

- Erst wissen, dann zeichnen: `vw2d_knowledge` (element=…, room=…,
  query=„dusche bewegungsfläche“, topic=…) liefert Stellflächen,
  Bewegungsflächen (Tiefe, größere Tiefe bei Gegenüber), seitliche Abstände
  (VDI 6000), Höhen, barrierefreie (120 × 120) und rollstuhlgerechte
  (150 × 150) Werte, Raumgrößen und Regeln (Treppe, Tür, Flur, Aufzug,
  Stellplatz, Rettungsweg, Büro, Schule …) – immer mit Buchseite. Werte so
  nennen, nicht schätzen.
- Raum planen: `vw2d_plan_room(width, depth, room=…, door={wall, offset,
  width, swing, hinge}, mode=standard|barrierefrei|rollstuhlgerecht)` –
  `offset` wie im Plan abgelesen: von der linken unteren Innenecke bis zur
  Öffnung (an Nord-/Südwand in x, an West-/Ostwand in y); `hinge` von innen
  gesehen (nach innen aufgehende Tür = DIN links/rechts). Erst
  ohne `draw`, Vorschau und Probleme zeigen, Variante wählen lassen, dann
  `draw=true, variant=n, origin=[x, y]` (linke untere Innenecke). Eine
  bestimmte Buchlösung: `layout=…` (vw2d_knowledge room=… listet sie).
- Gezeichneten Raum prüfen: `vw2d_check_room(room_uuid=…)`.
- Einzelne Elemente: `vw2d_place_element(element, position, wall=…)` mit
  Bewegungsfläche (abschaltbar mit `movement=false`).
- Darstellung gehört dem Nutzer: `vw2d_style` zeigt den Stil. Der
  Raumpilot-Look ist nur die erste Ebene. Wünscht der Nutzer seinen
  Bürostil: Stil speichern (Klassen, Farben, Bewegungsflächen an/aus) und
  Elemente eigenen Symbolen zuordnen (`symbols`: {element: {symbol,
  library, dx, dy, rotation}}). Nie ungefragt ändern.

## Ansichten und Schnitte (Sanitär, Küche, Möbel, Bäume, Personen, Autos)

- `vw2d_assets` / `vw2d_place_asset` mit `view` = `front` oder `side`
  (Boden bei y = 0); im Grundriss besser die Planungswerkzeuge oben.
- Ausrichtung mit `wall`: wo die Wand liegt, vom Objekt aus gesehen
  („above“, „left“, „below“, „right“) – das Objekt schaut dann in den Raum.
  Einsetzpunkt bei Wandobjekten: Mitte der Wandseite.
- Maße als `params` in mm; mehrere gleiche Objekte über `placements`;
  linke/rechte Variante mit `mirror`.

## Updates

- Steht in einer Werkzeug-Antwort „KEYSTONE UPDATE REQUIRED“ (bei `vw_ping`:
  „update_available“): vor dem Weiterarbeiten anhalten, dem Nutzer sagen,
  dass eine neue Version da ist, auf dem Update bestehen und sofort
  `vw_update` anbieten – lädt die neueste Erweiterung in „Downloads“ und
  öffnet den Installationsdialog; der Nutzer klickt „Aktualisieren“. Nur
  bei ausdrücklichem „nicht jetzt“ weitermachen und am Ende jeder Antwort
  erneut erinnern.
