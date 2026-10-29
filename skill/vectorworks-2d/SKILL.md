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
