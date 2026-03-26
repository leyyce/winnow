# 00 — System Overview & Architecture Guide

> **Stand:** Sprint 5.1 (Architektur-Baseline abgeschlossen)  
> **Kontext:** Bachelorarbeit — Quality Assurance (QA) und Governance Framework für Citizen-Science-Projekte

## 1. Vision & Systemgrenzen

**Das Problem:** Citizen-Science-Projekte sammeln massenhaft Daten (z. B. Baummessungen), deren Qualität stark variiert. Die Qualitätssicherung (QA) – also das Erkennen von Ausreißern, das Steuern von Review-Prozessen und das Bewerten von Nutzer-Zuverlässigkeit – wird oft hartcodiert in die jeweiligen Hauptanwendungen integriert. Das führt zu redundanter, schwer wartbarer Logik.

**Die Lösung (Winnow):** Winnow extrahiert diesen gesamten QA- und Governance-Prozess in einen spezialisierten, zustandslosen FastAPI-Microservice. Winnow fungiert als **"Validation & Governance Engine"**. Client-Systeme (wie eine Laravel-App) senden Messdaten an Winnow. Winnow bewertet die Plausibilität, orchestriert den notwendigen manuellen Review-Prozess und liefert nach Abschluss Empfehlungen zur Vertrauenswürdigkeit der Nutzer zurück. 

Winnow ist **projekt-agnostisch**: Durch reine Konfiguration kann es Baummessungen, Vogelbeobachtungen oder Wasserqualitätsprüfungen validieren, ohne dass der Kerncode geändert werden muss ("Configuration is King").

---

## 2. Grundlegende Architekturentscheidungen (ADLs)

Diese vier Kernentscheidungen bilden das Fundament der Architektur und verhindern typische Microservice-Anti-Patterns:

### Entscheidung 1: Strikte Domain Ownership (Keine geteilten Datenbanken)
* **Konzept:** Das Client-System (Laravel) ist der alleinige Besitzer der *Domänendaten* (Benutzeraccounts, Bäume, Fotos). Winnow besitzt ausschließlich den *Validierungs-Zustand* und die *Audit-Logs*.
* **Warum?** Die Verhinderung eines "Distributed Monolith". Wenn beide Systeme dieselbe Datenbank nutzen würden, wären sie stark gekoppelt. Winnow erhält alle nötigen Nutzer-Metadaten (Rolle, Trust-Level) bei jedem Request direkt mitgeliefert ("Data on the Wire").

### Entscheidung 2: 100% Immutability & Append-Only State (Triple-Snapshot-Pattern)
* **Konzept:** Das System löscht oder überschreibt niemals Daten. 
  1. `submissions`: Der eingehende Datensatz wird als unveränderlicher Snapshot gespeichert.
  2. `scoring_snapshots`: Die mathematische Bewertung der Pipeline wird eingefroren.
  3. `status_ledger`: Jeder Statuswechsel (z.B. pending → approved) ist ein neuer Datenbank-Eintrag.
* **Warum?** Revisionssicherheit (Audit-Log). Wenn ein User im Client-System seine Daten korrigiert, erzeugt dies eine *neue* Submission in Winnow, die über einen `supersedes`-Pointer logisch auf die alte verweist (Backward-Chaining). So entsteht eine lückenlose Historie ohne Datenverlust.

### Entscheidung 3: Winnow als Governance Authority (Serverseitiges Voting)
* **Konzept:** Anstatt dass das Client-System die Stimmen der Reviewer zählt, sammelt Winnow die Votes direkt. Winnow errechnet anhand des Confidence Scores, *welches Review-Tier* greift (z. B. "2 Experten-Votes oder 3 Citizen-Votes benötigt") und finalisiert die Submission automatisch, sobald die Schwellenwerte erreicht sind.
* **Warum?** Würde der Client die Stimmen zählen, müsste jedes neue Citizen-Science-Projekt komplexe Rollen- und Berechtigungslogiken neu programmieren. Winnow zentralisiert diese Logik. Der Client agiert lediglich als "Renderer" für die Aufgaben, die Winnow freigibt.

### Entscheidung 4: Event-Driven Feedback (Transactional Outbox Pattern)
* **Konzept:** Wenn eine Submission finalisiert wird, berechnet der "Trust Advisor" von Winnow eine Empfehlung (z. B. "+2 Trust-Punkte für den Einreicher"). Diese wird per Webhook an den Client gesendet. Die Erstellung dieses Webhook-Events geschieht in derselben Datenbank-Transaktion wie der Statuswechsel (Outbox Pattern).
* **Warum?** Netzwerke sind unzuverlässig. Fällt die Verbindung kurzzeitig aus, garantiert das Outbox-Pattern durch automatische Retries, dass kein Statuswechsel verloren geht und der Client garantiert benachrichtigt wird. Winnow *berät*, der Client *entscheidet*, ob er das Trust-Level des Users anpasst.

---

## 3. Workflow-Walkthrough: Integration eines Projekts ("Tree-App")

Wie sieht die Nutzung von Winnow in der Praxis aus? Hier der Ablauf am Beispiel einer App zur Baumerfassung:

### Schritt 1: Projekt-Onboarding (Konfiguration in Winnow)
Bevor Daten fließen, wird die "Tree-App" in Winnows Registry angemeldet. Dies geschieht rein deklarativ im Code (`ProjectBuilder`):
1. **Payload-Schema:** Ein Pydantic-Schema definiert, dass eine Baummessung z. B. `height` (Float) und `trunk_diameter` (Int) enthalten muss.
2. **Scoring-Rules:** Die Pipeline wird konfiguriert. Wir fügen den **Plausibilitäts-Faktor (P-Faktor)** hinzu (vergleicht die gemessene Höhe mit historischen Spezies-Durchschnitten) sowie einen Höhen- und Distanz-Faktor.
3. **Governance-Tiers:** Wir definieren die Spielregeln. Z. B. Ein Score über 80 darf vom System automatisch akzeptiert werden. Ein Score unter 50 braucht 2 Stimmen von "Experten" oder 3 Stimmen von vertrauenswürdigen "Citizens".

### Schritt 2: Dateneingabe & Scoring (Der Citizen Scientist)
1. User "Maria" erfasst einen Baum in der Laravel-App. 
2. Laravel verpackt die Daten (Höhe 18m, Spezies-Durchschnitt 20m) zusammen mit Marias Metadaten (Trust-Level: 3, Rolle: citizen) in ein "Envelope"-JSON und sendet es an Winnows `POST /submissions` Endpoint.
3. **Stage 1 (Validierung):** Winnow prüft strukturell. Ist die Höhe > 0? Fehlen Fotos? (Fail-Fast: Bei Fehlern wird sofort abgebrochen).
4. **Stage 2 (Scoring):** Die Daten sind valide. Die Pipeline läuft durch. Der P-Faktor gibt hohe Punkte, da 18m nah am Durchschnitt von 20m liegen. Winnow errechnet einen **Confidence Score von 75/100**.
5. **Ergebnis:** Winnow speichert alles als Immutable Snapshots und antwortet Laravel: *"Score ist 75. Status ist 'pending_review'. Laut Governance-Regeln wird 1 Experten-Review benötigt."*

### Schritt 3: Task Discovery (Der Review-Prozess)
1. Ein anderer Nutzer, "Dr. Wald" (Rolle: expert, Trust: 90), öffnet in der Laravel-App sein Dashboard.
2. Laravel fragt Winnow: `GET /tasks/available?user_role=expert&user_trust=90`.
3. Winnow gleicht Dr. Walds Metadaten mit den hinterlegten Governance-Tiers aller ausstehenden Submissions ab und liefert Marias Baum-Messung in der Liste zurück, da Dr. Wald berechtigt ist.

### Schritt 4: Voting & Finalisierung
1. Dr. Wald prüft die Fotos und klickt in Laravel auf "Approve". 
2. Laravel sendet den Vote an Winnow: `POST /submissions/{id}/votes`.
3. Winnow speichert den Vote (`submission_votes`). Die Governance-Engine prüft: *"Das Expert-Tier forderte 1 Experten-Gewicht. Dr. Wald hat 1 Experten-Gewicht beigesteuert. Schwellenwert erreicht!"*
4. Winnow hängt einen neuen Eintrag an den `status_ledger` an: Status wechselt von `pending_review` auf `approved`.

### Schritt 5: Trust Feedback (Der Loop schließt sich)
1. Da die Submission nun als Ground-Truth `approved` ist, springt Winnows **Trust Advisor** an.
2. Er analysiert Marias Historie in Winnow und entscheidet: *"Gute Messung, +2 Trust-Punkte"*.
3. Dieser `trust_delta` wird zusammen mit dem Event `submission.approved` im Transactional Outbox gespeichert.
4. Ein Hintergrund-Worker in Winnow sendet den Webhook an Laravel. Laravel empfängt die Nachricht und erhöht Marias Trust-Level in der eigenen Datenbank von 3 auf 5.

---

## 4. Deep Dives (Dokumentations-Referenzen)

Für detaillierte technische Spezifikationen und API-Verträge existieren folgende Begleitdokumente:

* **[`01_project_structure.md`](01_project_structure.md)** — Übersicht der Ordnerstruktur und Clean Architecture-Prinzipien (Domain vs. Infrastructure).
* **[`02_architecture_patterns.md`](02_architecture_patterns.md)** — Detaillierte Erklärung der Software-Design-Patterns (Strategy, Envelope, Registry).
* **[`03_api_contracts.md`](03_api_contracts.md)** — JSON-Payloads, Pydantic-Schemas, REST-Interfaces und der Webhook-Ablauf.
* **[`04_risk_analysis.md`](04_risk_analysis.md)** — Risiko-Matrix, Trade-offs und Mitigations-Strategien.
* **[`05_database_design.md`](05_database_design.md)** — Append-Only-Ledger-Modell, Trust-Delta-Berechnung via rekursiver CTEs und Outbox Pattern.