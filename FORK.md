# Databites-Fork von Databasus — Übersicht & Übergabe

Dieses Dokument erklärt den Fork [`databiteslab/databasus`](https://github.com/databiteslab/databasus) für Personen, die den Betrieb oder die Weiterentwicklung übernehmen. Es ergänzt das Upstream-[README](README.md) und beschreibt **nur die Databites-spezifischen Abweichungen**.

---

## Was ist Databasus?

**Databasus** ist ein freies, quelloffenes und selbst gehostetes Backup-Tool. Schwerpunkt ist **PG** (zusätzlich MySQL, MariaDB, MongoDB).

Typischer Ablauf:

1. Datenbanken in einem **Workspace** registrieren (Host, Port, DB-Name, Zugangsdaten).
2. Ein **Speicherziel** konfigurieren (lokal, S3, R2, SFTP, …).
3. Einen **Backup-Zeitplan** setzen (z. B. täglich um eine Uhrzeit) inkl. Aufbewahrung (Retention).
4. Optional **Benachrichtigungen** (E-Mail, Slack, Discord, Telegram, Webhook) bei Erfolg/Fehler.

Databasus erzeugt **logische** Dumps (`pg_dump`) und/oder **physische** Cluster-Backups inkl. WAL-Streaming für Point-in-Time Recovery. Die Web-UI und die REST-API laufen standardmäßig auf Port **4005**.

Offizielle Upstream-Doku: [databasus.com](https://databasus.com) · Upstream-Repo: [databasus/databasus](https://github.com/databasus/databasus).

---

## Was ist dieser Fork?

Der Fork betreibt Databasus für die **Databites-Multi-Tenant-PG-Umgebung** (viele Mandanten-Datenbanken auf gemeinsamen Hosts, große Analyse-/BI-Tabellen mit gemischter Großschreibung).

Ziele des Forks:

- Backup-Last und -Größe unter Kontrolle halten (globale Tabellen-Ausschlüsse).
- Hängende `pg_dump`-Sessions beenden, die Locks halten und Migrationen blockieren.
- Das eigene Container-Image aus der Org-Registry deployen.
- Optional: neue Mandanten-DBs automatisch in Databasus registrieren und Zeitpläne streuen.

Upstream wird weiterhin eingemerget (`Merge upstream/main into main`). Nach jedem Merge die Fork-Features unten gegenprüfen.

---

## Wichtige Änderungen gegenüber Upstream

### 1. Globale Tabellen-Ausschlüsse per Environment (Hauptfeature)

**Problem:** Viele Mandanten-DBs enthalten sehr große Analyse-/Aggregations-Tabellen. Ein vollständiges `pg_dump` wird dann zu groß und zu langsam.

**Lösung:** Globale Env-Variablen, die für **alle** PG-Logical-Backups gelten (zusätzlich zu eventuellen Exclude-Listen pro DB in der UI):

| Variable | Wirkung |
|---|---|
| `BACKUP_EXCLUDE_TABLES` | Komma-separierte Liste → `pg_dump --exclude-table=…` |
| `BACKUP_INCLUDE_TABLES` | Optional; wenn gesetzt, nur diese Tabellen (`--table=…`) |

Konfiguration in der **Server-Compose-Datei** (nicht im Dockerfile-Image selbst — die Liste steht in der Compose-Umgebung und wirkt beim Container-Start):

```16:17:docker-compose.server.yml
      # Comma-separated list of tables to exclude from all PG backups
      BACKUP_EXCLUDE_TABLES: 'public."Artikelstatistik",public."Liefertreue",...'
```

**Wichtige Details:**

- Tabellennamen mit Großbuchstaben müssen **quoted** sein: `public."Artikelstatistik"`.
- Wildcards (`*`, `?`) werden intern so normalisiert, dass Anführungszeichen bei Pattern-Matching nicht wörtlich mitgematcht werden.
- Code: `backend/internal/config/config.go`; Anwendung im Logical-Backup-Use-Case `create_backup_uc.go` (Unterordner `usecases/logical/…`).
- Die Liste in `docker-compose.server.yml` ist produktionskritisch. Entfernen oder leeren → riesige Tabellen landen wieder im Backup.

**Ändern der Ausschlussliste:**

1. Eintrag in `docker-compose.server.yml` unter `BACKUP_EXCLUDE_TABLES` anpassen.
2. Auf dem Server: `docker compose -f docker-compose.server.yml up -d` (Container neu erzeugen, damit die Env greift).
3. Test-Backup einer typischen Mandanten-DB prüfen (Größe/Dauer/Inhalt).

---

### 2. Watchdog für hängende `pg_dump`-Sessions

`pg_dump` deaktiviert eigene Statement-/Idle-Timeouts. Bleibt eine Session hängen, hält sie Locks (`AccessShareLock`) und kann Schema-Migrationen blockieren.

| Variable | Default | Bedeutung |
|---|---|---|
| `BACKUP_STALE_SESSION_WATCHDOG_ENABLED` | `true` | Watchdog an/aus |
| `BACKUP_STALE_SESSION_CHECK_INTERVAL_MINUTES` | `10` | Prüfintervall |
| `BACKUP_STALE_SESSION_MAX_DURATION_HOURS` | `12` | Ab diesem Alter: `pg_terminate_backend` (in der Praxis oft auf 2–4h senken) |
| `BACKUP_PG_DUMP_MAX_DURATION_HOURS` | `2` | Timeout auf Databasus-Seite für den Dump-Prozess |

Code: `backend/internal/features/backups/backups/backuping/logical/stale_session_watchdog.go`, Start in `backend/cmd/main.go`.

**Achtung:** Timeouts nicht unter die real längste legitime Backup-Dauer setzen — sonst schlagen echte Backups fehl.

---

### 3. Server-Deployment & eigenes Image

| Datei | Zweck |
|---|---|
| [`docker-compose.server.yml`](docker-compose.server.yml) | Produktions-Compose: Image, Port 4005, Volume, `BACKUP_EXCLUDE_TABLES` |
| [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml) | Baut und pusht nach `ghcr.io/databiteslab/databasus` (u. a. Tag `latest`) |

**Nicht** das öffentliche Docker-Hub-Image `databasus/databasus` für diesen Fork verwenden — dort fehlen die Databites-Anpassungen.

Start auf dem Server:

```bash
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d
```

Daten/Secrets liegen im Docker-Volume `databasus-data` (unter `/databasus-data` im Container). Volume-Backups sind sensibel.

---

### 4. Tenant-Backup-Sync (Ops-Skript)

Pfad: [`ops/tenant-backup-sync/`](ops/tenant-backup-sync/) — separates Python-Skript (`uv`), **nicht** Teil des Databasus-Containers.

Funktion:

1. Alle verbindbaren DBs auf dem PG-Host auflisten.
2. Mit in Databasus registrierten DBs vergleichen (`host` + `port` + `database`).
3. Fehlende Mandanten: Read-only-User anlegen → in Databasus registrieren → Backup-Config speichern.
4. Höchstens `MAX_IMMEDIATE_BACKUPS` Sofort-Backups (Default `1`); Rest wartet auf den Zeitplan.
5. Bei Fehler nach dem Anlegen: Databasus-Eintrag wieder löschen und RO-Rolle droppen.

**Zeitplan-Streuung (nur neue DBs):** statt einer festen Uhrzeit ein Fenster, z. B. `04:00`–`07:55`, Slot per `crc32(db_name)`. Bereits registrierte DBs werden **nicht** umgeplant.

Details, Env-Variablen und sichere Testreihenfolge: [`ops/tenant-backup-sync/README.md`](ops/tenant-backup-sync/README.md).

Das Skript muss auf dem Server (oder einem Cron-Host) separat ausgecheckt/kopiert werden — Compose allein reicht dafür nicht.

---

## Was Übernehmende wissen müssen

### Betrieb

| Thema | Hinweis |
|---|---|
| UI/API | Port **4005** |
| Image | Immer `ghcr.io/databiteslab/databasus:latest` (bzw. SHA-Tag) pullen |
| Compose | `docker-compose.server.yml` ist der Produktionsweg; `docker-compose.yml` ist eher Dev/Upstream |
| Tabellen-Exclude | In Compose pflegen; nach Änderung Container neu starten |
| Upstream-Merge | Danach Exclude-Parsing, Watchdog-Wiring und Compose prüfen |

### Sicherheit / Secrets

- Dedizierter Databasus-Automations-User für das Sync-Skript (kein persönliches Login).
- PG-Admin mit `CREATEROLE` nur für das Sync-Skript; `SSL_MODE` gilt auch für direkte Admin-Verbindungen des Skripts.
- Sync-`.env` mit `chmod 600`; Secrets nie loggen.
- Volume `databasus-data` enthält Schlüsselmaterial — Zugriff einschränken.

### Was man nicht „einfach“ ändern sollte

1. **`BACKUP_EXCLUDE_TABLES` leeren** — Backup-Last explodiert.
2. **Falsches Image** (Docker Hub Upstream) — Fork-Features fehlen still.
3. **Watchdog/Timeouts zu aggressiv** — gute Backups werden abgebrochen.
4. **Sync-Skript ohne Dry-Run** gegen viele fehlende DBs — massenhaft neue Rollen/Registrierungen.
5. **Annahme, Sync reschedule’t alte DBs** — tut es nicht; alte Fenster (z. B. 02:00–04:00) bleiben.

### Empfohlene erste Schritte bei Übergabe

1. UI öffnen, Workspace/Storage/Notifier und bestehende Zeitpläne ansehen.
2. `docker-compose.server.yml` und aktuelle `BACKUP_EXCLUDE_TABLES` lesen.
3. Ein manuelles Test-Backup einer kleinen Mandanten-DB; Größe/Dauer notieren.
4. Optional Sync: `DRY_RUN=true` → Canary mit `PG_EXCLUDE_DATABASES` / `MAX_IMMEDIATE_BACKUPS=0` → erst dann Cron (z. B. `30 8 * * *`).
5. Nach Upstream-Merge: Image bauen/publishen, Server `pull` + `up -d`, Exclude-Liste unverändert?

### Weiterführende Dateien

| Datei | Inhalt |
|---|---|
| [README.md](README.md) | Upstream-Produktbeschreibung & Features |
| [docker-compose.server.yml](docker-compose.server.yml) | Produktion + Tabellen-Ausschluss |
| [ops/tenant-backup-sync/README.md](ops/tenant-backup-sync/README.md) | Automations-Skript |
| [CLAUDE.md](CLAUDE.md) | Engineering-Regeln für Agenten/Beiträge im Repo |

---

## Kurzfassung

Dieser Fork ist Databasus für Databites: **gleiche Software**, plus globale Tabellen-Ausschlüsse über Compose-Env, ein Watchdog gegen hängende `pg_dump`-Sessions, Deployment über **GHCR der Org**, und optional ein Skript zur automatischen Registrierung neuer Mandanten mit gestaffelten Backup-Zeiten. Wer übernimmt, sollte zuerst Compose, Exclude-Liste und Image-Quelle verstehen — dort liegt der größte Betriebseinfluss.
