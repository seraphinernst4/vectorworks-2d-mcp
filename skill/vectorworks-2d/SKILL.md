---
name: vectorworks-2d
description: Exakte, sauber organisierte 2D-Architekturzeichnungen in Vectorworks mit den vw2d-Werkzeugen (Keystone for Vectorworks). Verwenden, wenn in einer offenen Vectorworks-Datei gezeichnet, geprüft, bereinigt oder nach Ebenen/Klassen organisiert werden soll.
---

# Exakt zeichnen in Vectorworks (2D)

Ziel: Pläne, die ein Architekturbüro so abgeben würde. Exakte Geometrie,
lückenlose Flächen, klare Ebenen- und Klassenstruktur.

## Grundregeln

1. **Exakt.** Koordinaten in Dokumenteinheiten oder mit `unit="mm"/"m"`.
   Nichts schätzen, nichts runden. Maßketten als `relative: true`.
   Bögen als echte Bögen (`path` mit `arc_to`), nie als Polygon-Annäherung.
   Nach jedem Zeichnen `all_exact` prüfen – Abweichung muss 0 sein.
2. **Lückenlos und ohne Überlappung.** Benachbarte Flächen teilen sich exakt
   ihre Kanten. Keine übereinanderliegenden Füllflächen, keine Doppellinien.
   Zusammengehörige Bauteile (ein Ring, eine Decke, ein Rost) sind **eine**
   geschlossene Fläche – Teile mit `vw2d_boolean` (union) vereinigen.
3. **Nichts verlieren.** Beim Nachzeichnen oder Bereinigen jede Kante und
   jede Stufe übernehmen. Lieber nachfragen als vereinfachen.
4. **Organisiert.**
   - Konstruktionsebenen trennen Geschosse/Zeichnungsbereiche (z. B. „EG“,
     „OG1“, „Lageplan“) und haben den richtigen Maßstab.
   - Klassen trennen Bauteile (Wände, Stützen, Öffnungen, Treppen,
     Ausstattung, Achsen, Bemaßung, Beschriftung, Schraffur …). Hierarchie
     mit Bindestrich („Wand-Tragend“).
   - Jedes Objekt in eine passende Klasse – nie in „Keine“.
   - Grafische Attribute **nach Klasse** (Standard). Eigene Attribute nur,
     wenn es wirklich nötig ist.
   - Erst `vw2d_structure` lesen und dem vorhandenen Standard folgen.
     Fehlendes mit `vw2d_setup` anlegen (die Vorlage „architektur“ nur, wenn
     es noch keinen Standard gibt und der Nutzer zustimmt).
5. **2D bleibt 2D.** In der geöffneten Datei arbeiten, nichts in andere
   Dateien oder Programme übertragen.

## Ablauf

1. `vw2d_structure` – Ebenen, Klassen, Probleme. Bei Bestandszeichnungen
   zusätzlich `vw2d_read` für die Geometrie.
2. Plan klären: Welche Ebene, welche Klassen, welche Maße? Bei Unklarheit
   kurz nachfragen.
3. `vw2d_draw` (mehrere Objekte in einem Aufruf, Klasse immer angeben).
4. `vw2d_check` auf der Ebene (`tolerance_mm` ca. 1; Klassen, die absichtlich
   überlappen – Möbel, Schraffuren – über `ignore_classes` auslassen).
5. Fehler mit `vw2d_fix` beheben:
   - `snap` für Spalte, knapp verfehlte Punkte und kleine Überlappungen
     (Bestand mit `fixed` festhalten),
   - `overlaps` für echte Überlappungen (`priority`, sonst gewinnt das obere
     Objekt),
   - `close`, `clean`, `duplicates`, `merge`, `by_class`.
   Danach erneut prüfen, bis keine Fehler mehr übrig sind.
6. Ergebnis zeigen: Prüfbild aus `vw2d_check`/`vw2d_preview` und
   `vw2d_select` (zoomt in Vectorworks auf die Objekte).
7. Kurz berichten: was gezeichnet wurde, in welchen Ebenen/Klassen, dass die
   Prüfung sauber ist.

## Tipps

- Wandstärken: Umriss zeichnen und `vw2d_offset` nutzen, oder Wände als
  geschlossene Polygone mit exakten Maßen.
- Öffnungen in Flächen: `holes` beim Zeichnen oder `vw2d_boolean` subtract.
- Punkt exakt auf Punkt verschieben: `vw2d_transform` mit `from_point` und
  `to_point`.
- Rückgängig für genau einen Schritt: `vw_revert_last`.
- Große Bestandspläne (z. B. aus Rhino importiert): erst prüfen, dann mit
  `snap` (Toleranz ruhig 2–5 mm) und `clean` bereinigen; organische
  Bestandsformen nur vereinfachen, nicht eckig machen.

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
