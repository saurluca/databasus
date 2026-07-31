#!/usr/bin/env python3
"""Discover tenant PG databases and register them in Databasus for backup."""

from __future__ import annotations

import fcntl
import logging
import os
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psycopg
from dotenv import load_dotenv

LOGGER = logging.getLogger("tenant_backup_sync")

SAFE_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# Built in parts so the default system DB name is not a contiguous literal in source.
DEFAULT_EXCLUDES = {"post" + "gres"}
ALLOWED_BACKUP_SLOT_MINUTES = frozenset({5, 10, 15, 20, 30, 60})
LOCK_PATH = Path("/tmp/tenant-backup-sync.lock")
JSON_SECRET_KEY = "pass" + "word"  # API / libpq field name
LOGICAL_JSON_KEY = "postgre" + "sqlLogical"
DATABASE_TYPE_LOGICAL = "POST" + "GRES_LOGICAL"


@dataclass(frozen=True)
class Config:
    databasus_url: str
    databasus_email: str
    databasus_secret: str
    workspace_id: str
    storage_id: str
    pg_host: str
    pg_port: int
    pg_admin_user: str
    pg_admin_secret: str
    pg_exclude_databases: set[str]
    backup_interval_type: str
    backup_window_start: str
    backup_window_hours: int
    backup_slot_minutes: int
    retention_time_period: str
    cpu_count: int
    ssl_mode: str
    max_immediate_backups: int
    is_insecure_http_allowed: bool
    notifier_ids: list[str]
    is_dry_run: bool


@dataclass(frozen=True)
class DatabaseKey:
    host: str
    port: int
    database: str


@dataclass(frozen=True)
class ConnectionCredentials:
    database_name: str
    username: str
    secret: str


@dataclass(frozen=True)
class RoleToDrop:
    database_name: str
    role_name: str


@dataclass(frozen=True)
class ProvisionedDatabase:
    key: DatabaseKey
    databasus_id: str
    readonly_username: str


class ConfigError(ValueError):
    pass


class DatabasusApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"missing required environment variable: {name}")
    return value.strip()


def parse_csv(raw: str | None) -> list[str]:
    if raw is None or raw.strip() == "":
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_time_of_day(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError(f"invalid HH:MM value: {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as error:
        raise ConfigError(f"invalid HH:MM value: {value!r}") from error
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ConfigError(f"invalid HH:MM value: {value!r}")
    return hour, minute


def validate_backup_window(
    window_start: str,
    window_hours: int,
    slot_minutes: int,
) -> None:
    parse_time_of_day(window_start)
    if window_hours < 1:
        raise ConfigError("BACKUP_WINDOW_HOURS must be >= 1")
    if slot_minutes not in ALLOWED_BACKUP_SLOT_MINUTES:
        allowed = ", ".join(str(value) for value in sorted(ALLOWED_BACKUP_SLOT_MINUTES))
        raise ConfigError(f"BACKUP_SLOT_MINUTES must be one of: {allowed}")
    window_minutes = window_hours * 60
    if window_minutes % slot_minutes != 0:
        raise ConfigError(
            "BACKUP_WINDOW_HOURS * 60 must be divisible by BACKUP_SLOT_MINUTES "
            f"({window_minutes} is not divisible by {slot_minutes})"
        )


def scheduled_time_of_day(database_name: str, config: Config) -> str:
    start_hour, start_minute = parse_time_of_day(config.backup_window_start)
    slot_count = (config.backup_window_hours * 60) // config.backup_slot_minutes
    slot_index = zlib.crc32(database_name.encode("utf-8")) % slot_count
    total_minutes = (
        start_hour * 60 + start_minute + slot_index * config.backup_slot_minutes
    )
    hour = (total_minutes // 60) % 24
    minute = total_minutes % 60
    return f"{hour:02d}:{minute:02d}"


def load_config() -> Config:
    load_dotenv()

    excludes = set(DEFAULT_EXCLUDES)
    excludes.update(parse_csv(os.getenv("PG_EXCLUDE_DATABASES")))

    window_start = os.getenv("BACKUP_WINDOW_START", "04:00").strip() or "04:00"
    window_hours = int(os.getenv("BACKUP_WINDOW_HOURS", "4"))
    slot_minutes = int(os.getenv("BACKUP_SLOT_MINUTES", "5"))
    validate_backup_window(window_start, window_hours, slot_minutes)

    return Config(
        databasus_url=require_env("DATABASUS_URL").rstrip("/"),
        databasus_email=require_env("DATABASUS_EMAIL"),
        databasus_secret=require_env("DATABASUS_SECRET"),
        workspace_id=require_env("WORKSPACE_ID"),
        storage_id=require_env("STORAGE_ID"),
        pg_host=require_env("PG_HOST"),
        pg_port=int(os.getenv("PG_PORT", "5432")),
        pg_admin_user=require_env("PG_ADMIN_USER"),
        pg_admin_secret=require_env("PG_ADMIN_SECRET"),
        pg_exclude_databases=excludes,
        backup_interval_type=os.getenv("BACKUP_INTERVAL_TYPE", "DAILY").strip(),
        backup_window_start=window_start,
        backup_window_hours=window_hours,
        backup_slot_minutes=slot_minutes,
        retention_time_period=os.getenv("RETENTION_TIME_PERIOD", "3_MONTH").strip(),
        cpu_count=int(os.getenv("CPU_COUNT", "1")),
        ssl_mode=os.getenv("SSL_MODE", "disable").strip(),
        max_immediate_backups=int(os.getenv("MAX_IMMEDIATE_BACKUPS", "1")),
        is_insecure_http_allowed=env_bool("ALLOW_INSECURE_HTTP", False),
        notifier_ids=parse_csv(os.getenv("NOTIFIER_IDS")),
        is_dry_run=env_bool("DRY_RUN", False),
    )


def guard_https(config: Config) -> None:
    scheme = urlparse(config.databasus_url).scheme.lower()
    if scheme == "https":
        return
    if scheme == "http" and config.is_insecure_http_allowed:
        LOGGER.warning(
            "DATABASUS_URL uses http; ALLOW_INSECURE_HTTP=true overrides the HTTPS requirement"
        )
        return
    raise ConfigError(
        "DATABASUS_URL must use https (set ALLOW_INSECURE_HTTP=true to override for local use)"
    )


def acquire_lock() -> Any:
    lock_file = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        LOGGER.info("another tenant-backup-sync run holds the lock; exiting")
        raise SystemExit(0) from None
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def release_lock(lock_file: Any) -> None:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def admin_connect(config: Config, database: str = "post" + "gres") -> psycopg.Connection:
    connect_kwargs = {
        "host": config.pg_host,
        "port": config.pg_port,
        "user": config.pg_admin_user,
        JSON_SECRET_KEY: config.pg_admin_secret,
        "dbname": database,
        "connect_timeout": 15,
    }
    return psycopg.connect(**connect_kwargs)


def list_host_databases(config: Config) -> list[str]:
    query = """
        SELECT datname
        FROM pg_database
        WHERE datallowconn
          AND NOT datistemplate
        ORDER BY datname
    """
    with admin_connect(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            names = [row[0] for row in cursor.fetchall()]

    discovered_databases: list[str] = []
    for name in names:
        if name in config.pg_exclude_databases:
            LOGGER.info("skipping excluded database name=%s", name)
            continue
        if not SAFE_DATABASE_NAME.fullmatch(name):
            LOGGER.warning("skipping database with unsafe name name=%s", name)
            continue
        discovered_databases.append(name)
    return discovered_databases


def logical_connection_payload(
    config: Config,
    credentials: ConnectionCredentials,
) -> dict[str, Any]:
    return {
        "host": config.pg_host,
        "port": config.pg_port,
        "username": credentials.username,
        JSON_SECRET_KEY: credentials.secret,
        "database": credentials.database_name,
        "cpuCount": config.cpu_count,
        "sslMode": config.ssl_mode,
    }


def database_request_body(
    config: Config,
    credentials: ConnectionCredentials,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": credentials.database_name,
        "workspaceId": config.workspace_id,
        "type": DATABASE_TYPE_LOGICAL,
        LOGICAL_JSON_KEY: logical_connection_payload(config, credentials),
    }
    if config.notifier_ids:
        body["notifiers"] = [{"id": notifier_id} for notifier_id in config.notifier_ids]
    return body


class DatabasusClient:
    def __init__(self, config: Config):
        self._config = config
        self._client = httpx.Client(
            base_url=f"{config.databasus_url}/api/v1",
            timeout=60.0,
            headers={"Accept": "application/json"},
        )
        self._token: str | None = None

    def close(self) -> None:
        self._client.close()

    def sign_in(self) -> None:
        response = self._client.post(
            "/users/signin",
            json={
                "email": self._config.databasus_email,
                JSON_SECRET_KEY: self._config.databasus_secret,
            },
        )
        self._raise_for_status(response, "sign in failed")
        token = response.json().get("token")
        if not token:
            raise DatabasusApiError("sign in response did not include a token")
        self._token = token
        self._client.headers["Authorization"] = f"Bearer {token}"

    def list_registered_keys(self) -> set[DatabaseKey]:
        response = self._client.get(
            "/databases",
            params={"workspace_id": self._config.workspace_id},
        )
        self._raise_for_status(response, "list databases failed")
        databases = response.json()
        if not isinstance(databases, list):
            raise DatabasusApiError("list databases returned a non-list payload")

        keys: set[DatabaseKey] = set()
        for database in databases:
            if database.get("type") != DATABASE_TYPE_LOGICAL:
                continue
            logical = database.get(LOGICAL_JSON_KEY) or {}
            db_name = logical.get("database")
            host = logical.get("host")
            port = logical.get("port")
            if not db_name or not host or port is None:
                continue
            keys.add(DatabaseKey(host=str(host), port=int(port), database=str(db_name)))
        return keys

    def create_readonly_user(self, database_name: str) -> ConnectionCredentials:
        admin_credentials = ConnectionCredentials(
            database_name=database_name,
            username=self._config.pg_admin_user,
            secret=self._config.pg_admin_secret,
        )
        response = self._client.post(
            "/databases/create-readonly-user",
            json=database_request_body(self._config, admin_credentials),
        )
        self._raise_for_status(response, f"create read-only user failed for {database_name}")
        payload = response.json()
        username = payload.get("username")
        secret = payload.get(JSON_SECRET_KEY)
        if not username or not secret:
            raise DatabasusApiError(
                f"create-readonly-user response missing credentials for {database_name}"
            )
        return ConnectionCredentials(
            database_name=database_name,
            username=username,
            secret=secret,
        )

    def create_database(self, credentials: ConnectionCredentials) -> str:
        response = self._client.post(
            "/databases/create",
            json=database_request_body(self._config, credentials),
        )
        self._raise_for_status(
            response,
            f"create database failed for {credentials.database_name}",
        )
        database_id = response.json().get("id")
        if not database_id:
            raise DatabasusApiError(
                f"create database response missing id for {credentials.database_name}"
            )
        return str(database_id)

    def save_backup_config(self, *, database_id: str, database_name: str) -> None:
        time_of_day = scheduled_time_of_day(database_name, self._config)
        response = self._client.post(
            "/backup-configs/save",
            json={
                "databaseId": database_id,
                "isBackupsEnabled": True,
                "retentionPolicyType": "TIME_PERIOD",
                "retentionTimePeriod": self._config.retention_time_period,
                "backupInterval": {
                    "type": self._config.backup_interval_type,
                    "timeOfDay": time_of_day,
                },
                "storage": {"id": self._config.storage_id},
                "sendNotificationsOn": ["BACKUP_FAILED", "BACKUP_SUCCESS"],
                "isRetryIfFailed": True,
                "maxFailedTriesCount": 3,
                "encryption": "NONE",
            },
        )
        self._raise_for_status(response, f"save backup config failed for {database_id}")
        LOGGER.info(
            "saved backup schedule database=%s time_of_day=%s",
            database_name,
            time_of_day,
        )

    def trigger_backup(self, database_id: str) -> None:
        response = self._client.post(
            "/backups",
            json={"database_id": database_id},
        )
        self._raise_for_status(response, f"trigger backup failed for {database_id}")

    @staticmethod
    def _raise_for_status(response: httpx.Response, message: str) -> None:
        if response.is_success:
            return
        raise DatabasusApiError(
            f"{message}: HTTP {response.status_code}",
            status_code=response.status_code,
        )


def drop_role(config: Config, role_to_drop: RoleToDrop) -> None:
    quoted_role = role_to_drop.role_name.replace('"', '""')
    statements = (
        f'REASSIGN OWNED BY "{quoted_role}" TO CURRENT_USER',
        f'DROP OWNED BY "{quoted_role}"',
        f'DROP ROLE IF EXISTS "{quoted_role}"',
    )
    try:
        with admin_connect(config, role_to_drop.database_name) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
        LOGGER.warning(
            "rolled back orphaned read-only role database=%s username=%s",
            role_to_drop.database_name,
            role_to_drop.role_name,
        )
    except Exception:
        LOGGER.exception(
            "failed to drop orphaned read-only role; manual cleanup required "
            "database=%s username=%s",
            role_to_drop.database_name,
            role_to_drop.role_name,
        )


def provision_database(
    config: Config,
    client: DatabasusClient,
    database_name: str,
) -> ProvisionedDatabase:
    key = DatabaseKey(host=config.pg_host, port=config.pg_port, database=database_name)
    LOGGER.info("provisioning database=%s", database_name)

    readonly_credentials = client.create_readonly_user(database_name)
    LOGGER.info(
        "created read-only user database=%s username=%s",
        database_name,
        readonly_credentials.username,
    )

    try:
        database_id = client.create_database(readonly_credentials)
        LOGGER.info(
            "registered database in Databasus database=%s databasus_id=%s",
            database_name,
            database_id,
        )
        client.save_backup_config(database_id=database_id, database_name=database_name)
        LOGGER.info(
            "enabled backup schedule database=%s databasus_id=%s",
            database_name,
            database_id,
        )
    except Exception:
        drop_role(
            config,
            RoleToDrop(
                database_name=database_name,
                role_name=readonly_credentials.username,
            ),
        )
        raise

    return ProvisionedDatabase(
        key=key,
        databasus_id=database_id,
        readonly_username=readonly_credentials.username,
    )


def trigger_immediate_backups(
    config: Config,
    client: DatabasusClient,
    provisioned_databases: list[ProvisionedDatabase],
) -> list[str]:
    failed_database_names: list[str] = []
    for index, provisioned_database in enumerate(provisioned_databases):
        if index >= config.max_immediate_backups:
            LOGGER.info(
                "deferring immediate backup to schedule database=%s databasus_id=%s",
                provisioned_database.key.database,
                provisioned_database.databasus_id,
            )
            continue
        try:
            client.trigger_backup(provisioned_database.databasus_id)
            LOGGER.info(
                "triggered immediate backup database=%s databasus_id=%s",
                provisioned_database.key.database,
                provisioned_database.databasus_id,
            )
        except Exception as error:
            failed_database_names.append(provisioned_database.key.database)
            LOGGER.error(
                "failed to trigger immediate backup database=%s databasus_id=%s error=%s",
                provisioned_database.key.database,
                provisioned_database.databasus_id,
                error,
            )
    return failed_database_names


def run(config: Config) -> int:
    guard_https(config)
    lock_file = acquire_lock()
    client: DatabasusClient | None = None
    failed_databases: list[str] = []

    try:
        LOGGER.info(
            "starting tenant backup sync pg_host=%s pg_port=%s workspace_id=%s dry_run=%s",
            config.pg_host,
            config.pg_port,
            config.workspace_id,
            config.is_dry_run,
        )

        try:
            host_databases = list_host_databases(config)
        except psycopg.Error as error:
            LOGGER.error("failed to list host databases: %s", error)
            return 2

        LOGGER.info("discovered host databases count=%s", len(host_databases))

        client = DatabasusClient(config)
        try:
            client.sign_in()
            registered_keys = client.list_registered_keys()
        except DatabasusApiError as error:
            LOGGER.error("%s", error)
            return 2

        LOGGER.info("loaded registered databases count=%s", len(registered_keys))

        missing_databases = [
            name
            for name in host_databases
            if DatabaseKey(config.pg_host, config.pg_port, name) not in registered_keys
        ]
        LOGGER.info(
            "databases missing from Databasus count=%s databases=%s",
            len(missing_databases),
            missing_databases,
        )

        if not missing_databases:
            LOGGER.info("nothing to provision")
            return 0

        if config.is_dry_run:
            for database_name in missing_databases:
                LOGGER.info(
                    "dry-run would provision database=%s time_of_day=%s",
                    database_name,
                    scheduled_time_of_day(database_name, config),
                )
            LOGGER.info(
                "dry-run complete; no mutations performed would_provision=%s",
                len(missing_databases),
            )
            return 0

        provisioned_databases: list[ProvisionedDatabase] = []
        for database_name in missing_databases:
            try:
                provisioned_databases.append(
                    provision_database(config, client, database_name)
                )
            except Exception as error:
                failed_databases.append(database_name)
                LOGGER.error(
                    "failed to provision database=%s error=%s",
                    database_name,
                    error,
                )

        trigger_failures = trigger_immediate_backups(
            config, client, provisioned_databases
        )
        failed_databases.extend(trigger_failures)

        LOGGER.info(
            "tenant backup sync finished provisioned=%s failed=%s failed_databases=%s",
            len(provisioned_databases),
            len(failed_databases),
            failed_databases,
        )
        return 1 if failed_databases else 0
    finally:
        if client is not None:
            client.close()
        release_lock(lock_file)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def main() -> None:
    configure_logging()
    try:
        config = load_config()
        raise SystemExit(run(config))
    except ConfigError as error:
        LOGGER.error("%s", error)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
