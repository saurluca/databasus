# Tenant backup sync

Python (`uv`) script that discovers PG databases on a host, registers any
that are missing from Databasus, creates a read-only backup user for each, enables
the backup schedule, and triggers a capped number of immediate first backups.

## Setup

```bash
cd ops/tenant-backup-sync
uv sync
cp .env.example .env
chmod 600 .env
# fill in .env
```

## Run

```bash
uv run sync_tenant_backups.py
```

Dry-run (lists missing databases and their would-be `time_of_day`; no Databasus writes and no role creation):

```bash
DRY_RUN=true uv run sync_tenant_backups.py
```

Local Databasus over plain HTTP (dev only):

```bash
ALLOW_INSECURE_HTTP=true DATABASUS_URL=http://127.0.0.1:4005 uv run sync_tenant_backups.py
```

## Required environment

| Variable | Purpose |
|---|---|
| `DATABASUS_URL` | Base URL, must be `https://` unless `ALLOW_INSECURE_HTTP=true` |
| `DATABASUS_EMAIL` / `DATABASUS_SECRET` | Dedicated automation account (not a personal login) |
| `WORKSPACE_ID` | Target workspace UUID |
| `STORAGE_ID` | Shared backup storage UUID |
| `PG_HOST` / `PG_PORT` / `PG_ADMIN_USER` / `PG_ADMIN_SECRET` | Admin connection with `CREATEROLE` |

Schedule defaults for **newly registered** databases:

| Variable | Default | Purpose |
|---|---|---|
| `BACKUP_INTERVAL_TYPE` | `DAILY` | Databasus interval type |
| `BACKUP_WINDOW_START` | `04:00` | Start of the daily schedule window (`HH:MM`) |
| `BACKUP_WINDOW_HOURS` | `4` | Window length in hours (≥ 1) |
| `BACKUP_SLOT_MINUTES` | `5` | Slot size; one of `5`, `10`, `15`, `20`, `30`, `60` |
| `MAX_IMMEDIATE_BACKUPS` | `1` | Cap on first backups triggered in this run |

`timeOfDay` is derived as `crc32(database_name) % slot_count` inside the window (stable across re-runs). With the defaults that is 48 slots across `04:00`–`07:55`. Already-registered databases are left unchanged (including any existing `02:00`–`04:00` schedules).

`SSL_MODE` applies to both Databasus-registered backup connections and this script’s direct admin PG connections (`list` / role rollback). Other defaults: `3_MONTH` retention.

## Behavior

1. Lists host databases with `datallowconn AND NOT datistemplate`
2. Skips the default system DB, anything in `PG_EXCLUDE_DATABASES`, and names outside `^[A-Za-z0-9_-]+$`
3. Diffs against Databasus on `(host, port, database)`
4. For each missing DB: create read-only user → register → enable backup config with a hash-staggered `timeOfDay`
5. On create/config failure: deletes the Databasus registration (if created) then drops the just-created role (logs IDs if either cleanup fails)
6. Triggers at most `MAX_IMMEDIATE_BACKUPS` first backups sequentially; the rest wait for the schedule

Overlapping cron runs exit immediately via `/tmp/tenant-backup-sync.lock`.

## Cron example

Prefer running after the overnight backup peak so new first-backups do not pile onto it:

```cron
30 8 * * * cd /path/to/repo/ops/tenant-backup-sync && /path/to/uv run sync_tenant_backups.py >> /var/log/tenant-backup-sync.log 2>&1
```

## Notes

- Prefer a dedicated Databasus automation user; sign-in JWTs are long-lived.
- The automation account needs workspace Owner, Admin, or Member.
- Secrets and tokens are never logged.
- This script does not reschedule already-registered databases.
- Heavy tenants can still be excluded via `PG_EXCLUDE_DATABASES` or adjusted in the Databasus UI.
- This script does not clean up orphaned `databasus-*` roles from earlier manual runs.
