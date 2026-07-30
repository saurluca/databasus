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

Dry-run (lists missing databases; no Databasus writes and no role creation):

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

Defaults: daily backups at `04:00`, `3_MONTH` retention, `MAX_IMMEDIATE_BACKUPS=3`.

## Behavior

1. Lists host databases with `datallowconn AND NOT datistemplate`
2. Skips the default system DB, anything in `PG_EXCLUDE_DATABASES`, and names outside `^[A-Za-z0-9_-]+$`
3. Diffs against Databasus on `(host, port, database)`
4. For each missing DB: create read-only user → register → enable backup config
5. On create/config failure: drops the just-created role (logs username if drop fails)
6. Triggers at most `MAX_IMMEDIATE_BACKUPS` first backups sequentially; the rest wait for the schedule

Overlapping cron runs exit immediately via `/tmp/tenant-backup-sync.lock`.

## Cron example

```cron
*/15 * * * * cd /path/to/repo/ops/tenant-backup-sync && /path/to/uv run sync_tenant_backups.py >> /var/log/tenant-backup-sync.log 2>&1
```

## Notes

- Prefer a dedicated Databasus automation user; sign-in JWTs are long-lived.
- The automation account needs workspace Owner, Admin, or Member.
- Secrets and tokens are never logged.
- This script does not clean up orphaned `databasus-*` roles from earlier manual runs.
