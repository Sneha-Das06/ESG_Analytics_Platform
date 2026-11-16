"""Tenant-isolated SQLite services for the ESG Management System.

All tenant reads and writes are scoped here. UI visibility is not an authorization
boundary: every public operation reloads the actor from the database and checks
their current role, company ownership, department assignment, and status.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import base64
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
_configured_db_path = os.getenv("ESG_DATABASE_PATH", "").strip()
DB_PATH_EXPLICIT = bool(_configured_db_path)
if DB_PATH_EXPLICIT:
    _configured_db_path = os.path.expandvars(os.path.expanduser(_configured_db_path))
    DB_PATH = Path(_configured_db_path)
    if not DB_PATH.is_absolute():
        DB_PATH = ROOT / DB_PATH
else:
    DB_PATH = ROOT / "data" / "esg_management.sqlite3"
DB_PATH = DB_PATH.resolve()
SCHEMA_VERSION = 5
ROLES = {"platform_super_admin", "company_manager", "normal_user", "viewer"}
PASSWORD_ITERATIONS = 310_000


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def _verify_password(password, encoded):
    try:
        algorithm, salt, expected = encoded.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = _password_hash(password, bytes.fromhex(salt)).split("$")[2]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def _credential_version(password_hash, mfa_secret_enc=None):
    """Return a non-reversible session marker that changes when credentials change."""
    return hashlib.sha256((password_hash + "\0" + (mfa_secret_enc or "")).encode("utf-8")).hexdigest()


def _backup_before_upgrade(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    backup_dir = path.resolve().parent / "backups"
    target = backup_dir / ("tenant_upgrade_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".sqlite3")
    return backup_database(target, path)


def database_backup_roots(path=None):
    """Return active and legacy backup roots, preserving discoverability after relocation."""
    active_root = Path(path or DB_PATH).resolve().parent / "backups"
    legacy_root = ROOT / "data" / "backups"
    roots = []
    for root in (active_root, legacy_root):
        resolved = root.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def discover_database_backups(path=None):
    """List SQLite backups from the active location and the legacy project location."""
    candidates = {}
    for root in database_backup_roots(path):
        if root.is_dir():
            for item in root.rglob("*.sqlite3"):
                if ".restore.tmp" not in item.name:
                    candidates[item.resolve()] = item.resolve()
    return sorted(candidates.values(), key=lambda item: item.stat().st_mtime, reverse=True)


def backup_database(destination=None, path=None):
    source = Path(path or DB_PATH)
    backup_root = source.resolve().parent / "backups"
    target = Path(destination or backup_root / ("esg_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".sqlite3"))
    target.parent.mkdir(parents=True, exist_ok=True)
    with connect(source) as src:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    return target


def _verify_database_file(path):
    candidate = Path(path)
    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise ValueError("Database backup is missing or empty.")
    check = sqlite3.connect(str(candidate), timeout=30)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError("Database integrity check failed: " + str(result))
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"companies", "users", "legacy_dataset_rows", "esg_records"}.issubset(tables):
            raise ValueError("Backup is not a complete ESG platform database.")
    finally:
        check.close()
    return True


def restore_database(platform_admin, backup_path, confirmation, path=None):
    """Restore a verified platform backup; only a Platform Super Admin can invoke it."""
    if confirmation != "RESTORE DATABASE":
        raise ValueError("Type RESTORE DATABASE to confirm this operation.")
    target = Path(path or DB_PATH).resolve()
    allowed_backup_roots = database_backup_roots(target)
    source = Path(backup_path).resolve()
    if not any(source == allowed_root or allowed_root in source.parents for allowed_root in allowed_backup_roots):
        raise ValueError("Choose a backup stored under an approved database backup folder.")
    if source == target:
        raise ValueError("The active database cannot be used as its own restore source.")
    with connect(target) as conn:
        actor = _actor(conn, platform_admin)
        if actor["role"] != "platform_super_admin":
            raise PermissionError("Only the Platform Super Admin can restore the platform database.")
    _verify_database_file(source)
    restore_backup_dir = target.parent / "backups"
    restore_backup_dir.mkdir(parents=True, exist_ok=True)
    current_backup = backup_database(
        restore_backup_dir / ("before_restore_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".sqlite3"),
        target,
    )
    _verify_database_file(current_backup)
    temp_restore = target.with_name(target.name + ".restore.tmp")
    try:
        backup_database(temp_restore, source)
        _verify_database_file(temp_restore)
        os.replace(temp_restore, target)
        initialize_database(target)
        with connect(target) as conn:
            restored_actor = conn.execute("SELECT id FROM users WHERE id=? AND role='platform_super_admin'", (actor["id"],)).fetchone()
            _audit(conn, restored_actor["id"] if restored_actor else None, "database_restored", "database", None,
                   f"source={source};pre_restore_backup={current_backup}")
    finally:
        if temp_restore.exists():
            temp_restore.unlink()
    return {"restored_from": source, "pre_restore_backup": current_backup}


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _add_column(conn, table, name, declaration):
    if name not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}')


def _rebuild_users_for_tenant_roles(conn):
    if not _table_exists(conn, "users"):
        conn.execute("""CREATE TABLE users(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('platform_super_admin','company_manager','normal_user','viewer')),
            status TEXT NOT NULL DEFAULT 'approved' CHECK(status IN ('pending','approved','rejected','disabled')),
            company_id INTEGER REFERENCES companies(id), department_id INTEGER REFERENCES departments(id),
            can_edit_esg INTEGER NOT NULL DEFAULT 0 CHECK(can_edit_esg IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        return

    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0] or ""
    cols = _columns(conn, "users")
    required = {"id", "name", "email", "password_hash", "role", "status", "company_id", "department_id", "can_edit_esg", "created_at", "updated_at"}
    if required.issubset(cols) and "company_manager" in sql and "platform_super_admin" in sql:
        return

    rows = [dict(row) for row in conn.execute("SELECT * FROM users")]
    legacy_permissions = {}
    if _table_exists(conn, "permissions"):
        pcols = _columns(conn, "permissions")
        if {"user_id", "company_id"}.issubset(pcols):
            for row in conn.execute("SELECT * FROM permissions"):
                legacy_permissions.setdefault(row["user_id"], []).append(dict(row))

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP TABLE IF EXISTS users_tenant_upgrade")
        conn.execute("""CREATE TABLE users_tenant_upgrade(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('platform_super_admin','company_manager','normal_user','viewer')),
            status TEXT NOT NULL DEFAULT 'approved' CHECK(status IN ('pending','approved','rejected','disabled')),
            company_id INTEGER REFERENCES companies(id), department_id INTEGER REFERENCES departments(id),
            can_edit_esg INTEGER NOT NULL DEFAULT 0 CHECK(can_edit_esg IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        for row in rows:
            role = row.get("role", "normal_user")
            role = {"super_admin": "platform_super_admin", "company_admin": "company_manager", "government_viewer": "viewer"}.get(role, role)
            if role not in ROLES:
                role = "normal_user"
            company_id = row.get("company_id")
            grants = legacy_permissions.get(row.get("id"), [])
            if company_id is None and role == "normal_user" and len(grants) == 1:
                company_id = grants[0]["company_id"]
            can_edit = int(row.get("can_edit_esg", 0) or 0)
            if grants:
                can_edit = max([can_edit] + [int(g.get("can_edit", 0) or 0) for g in grants])
            created = row.get("created_at") or now()
            status = row.get("status") or "approved"
            if status not in ("pending", "approved", "rejected", "disabled"):
                status = "approved"
            conn.execute("""INSERT INTO users_tenant_upgrade(id,name,email,password_hash,role,status,company_id,department_id,can_edit_esg,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (row["id"], row.get("name") or "User", row["email"], row["password_hash"], role, status,
                company_id, row.get("department_id"), can_edit, created, row.get("updated_at") or created))
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_tenant_upgrade RENAME TO users")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

def initialize_database(path=None, backup_existing=True, allow_create=False):
    target = Path(path or DB_PATH)
    configured_target = target.resolve() == DB_PATH.resolve() and DB_PATH_EXPLICIT
    if configured_target and not target.exists() and not allow_create:
        raise FileNotFoundError(
            f"Configured ESG database was not found at {target}. Refusing to create an empty database. "
            "After confirming this is an intentional first-time setup, run `python -m database init`."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        probe = sqlite3.connect(str(target))
        try:
            version = probe.execute("PRAGMA user_version").fetchone()[0]
            has_tables = probe.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
        finally:
            probe.close()
        if has_tables and version >= SCHEMA_VERSION:
            return target
        if backup_existing and has_tables and version < SCHEMA_VERSION:
            _backup_before_upgrade(target)

    with connect(target) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS companies(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, dataset_path TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), website TEXT NOT NULL DEFAULT '',
            industry TEXT NOT NULL DEFAULT '', manager_user_id INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')""")
        _add_column(conn, "companies", "active", "INTEGER NOT NULL DEFAULT 1")
        _add_column(conn, "companies", "website", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "companies", "industry", "TEXT NOT NULL DEFAULT ''")
        # Registration profile fields are additive so legacy tenants retain their data.
        for column, declaration in (
            ("legal_name", "TEXT NOT NULL DEFAULT ''"),
            ("registration_number", "TEXT NOT NULL DEFAULT ''"),
            ("company_type", "TEXT NOT NULL DEFAULT ''"),
            ("company_size", "TEXT NOT NULL DEFAULT ''"),
            ("country", "TEXT NOT NULL DEFAULT ''"),
            ("state", "TEXT NOT NULL DEFAULT ''"),
            ("city", "TEXT NOT NULL DEFAULT ''"),
            ("registered_address", "TEXT NOT NULL DEFAULT ''"),
            ("postal_code", "TEXT NOT NULL DEFAULT ''"),
            ("official_email", "TEXT NOT NULL DEFAULT ''"),
            ("official_phone", "TEXT NOT NULL DEFAULT ''"),
            ("logo_blob", "BLOB"),
            ("financial_reporting_year", "TEXT NOT NULL DEFAULT ''"),
            ("registration_terms_accepted", "INTEGER NOT NULL DEFAULT 0"),
            ("registration_declaration_accepted", "INTEGER NOT NULL DEFAULT 0"),
        ):
            _add_column(conn, "companies", column, declaration)
        _add_column(conn, "companies", "manager_user_id", "INTEGER REFERENCES users(id)")
        _add_column(conn, "companies", "created_at", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "companies", "updated_at", "TEXT NOT NULL DEFAULT ''")

        # Departments must exist before the users table rebuild installs its FK.
        conn.execute("""CREATE TABLE IF NOT EXISTS departments(
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
            name TEXT NOT NULL COLLATE NOCASE, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(company_id,name))""")
        _rebuild_users_for_tenant_roles(conn)
        _add_column(conn, "users", "designation", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "users", "mobile_number", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "users", "username", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "users", "mfa_enabled", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "users", "mfa_secret_enc", "TEXT")
        _add_column(conn, "users", "mfa_recovery_hashes", "TEXT NOT NULL DEFAULT '[]'")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS company_registration_number_unique ON companies(registration_number COLLATE NOCASE) WHERE registration_number<>''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS company_official_email_unique ON companies(official_email COLLATE NOCASE) WHERE official_email<>''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username COLLATE NOCASE) WHERE username<>''")

        conn.execute("""CREATE TABLE IF NOT EXISTS permissions(
            user_id INTEGER NOT NULL REFERENCES users(id), company_id INTEGER NOT NULL REFERENCES companies(id),
            can_edit INTEGER NOT NULL DEFAULT 0 CHECK(can_edit IN (0,1)), PRIMARY KEY(user_id,company_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS esg_records(
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), department_id INTEGER REFERENCES departments(id),
            financial_year TEXT NOT NULL, energy_kwh REAL NOT NULL CHECK(energy_kwh>=0), water_m3 REAL NOT NULL CHECK(water_m3>=0),
            carbon_tco2e REAL NOT NULL CHECK(carbon_tco2e>=0), waste_tonnes REAL NOT NULL CHECK(waste_tonnes>=0),
            renewable_pct REAL NOT NULL CHECK(renewable_pct BETWEEN 0 AND 100), social_score REAL NOT NULL CHECK(social_score BETWEEN 0 AND 100),
            employee_info TEXT NOT NULL DEFAULT '', governance_info TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
            created_by INTEGER REFERENCES users(id), updated_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(company_id,department_id,financial_year))""")
        _add_column(conn, "esg_records", "department_id", "INTEGER REFERENCES departments(id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS legacy_dataset_rows(
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), department_id INTEGER REFERENCES departments(id),
            dataset_path TEXT NOT NULL, source_hash TEXT NOT NULL, sheet_name TEXT NOT NULL, row_number INTEGER NOT NULL,
            payload_json TEXT NOT NULL, imported_at TEXT NOT NULL,
            UNIQUE(company_id,dataset_path,source_hash,sheet_name,row_number))""")
        _add_column(conn, "legacy_dataset_rows", "department_id", "INTEGER REFERENCES departments(id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
            entity_type TEXT NOT NULL, entity_id TEXT, details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS password_resets(
            email TEXT PRIMARY KEY COLLATE NOCASE, token_hash TEXT NOT NULL, expires_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS auth_rate_limits(
            scope TEXT NOT NULL,subject_hash TEXT NOT NULL,window_started REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,locked_until REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(scope,subject_hash))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS mfa_enrollment_challenges(
            user_id INTEGER PRIMARY KEY REFERENCES users(id),token_hash TEXT NOT NULL,
            secret_enc TEXT NOT NULL,expires_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS auth_sessions(
            token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
            credential_version TEXT NOT NULL,expires_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS migration_log(
            id INTEGER PRIMARY KEY, source TEXT NOT NULL UNIQUE, status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cleaning_history(
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
            department_id INTEGER REFERENCES departments(id), actor_user_id INTEGER NOT NULL REFERENCES users(id),
            source_name TEXT NOT NULL, source_hash TEXT NOT NULL, operation TEXT NOT NULL,
            changed_records INTEGER NOT NULL DEFAULT 0, removed_records INTEGER NOT NULL DEFAULT 0,
            skipped_records INTEGER NOT NULL DEFAULT 0, flagged_records INTEGER NOT NULL DEFAULT 0,
            backup_path TEXT NOT NULL, original_path TEXT NOT NULL DEFAULT '', cleaned_path TEXT NOT NULL DEFAULT '',
            report_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS cleaning_history_tenant_time ON cleaning_history(company_id,department_id,created_at)")
        conn.execute("""CREATE TABLE IF NOT EXISTS cleaning_backups(
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
            department_id INTEGER REFERENCES departments(id), actor_user_id INTEGER NOT NULL REFERENCES users(id),
            backup_path TEXT NOT NULL, created_at TEXT NOT NULL, used_at TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS cleaning_backup_actor_scope ON cleaning_backups(actor_user_id,company_id,department_id,used_at)")

        for row in conn.execute("SELECT id,created_at,updated_at FROM companies"):
            created = row["created_at"] or now()
            conn.execute("UPDATE companies SET created_at=?,updated_at=? WHERE id=?", (created, row["updated_at"] or created, row["id"]))
            conn.execute("INSERT OR IGNORE INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)", (row["id"], "General", created, created))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_company_per_manager ON companies(manager_user_id) WHERE manager_user_id IS NOT NULL")

        for user in conn.execute("SELECT id,role,company_id,department_id,can_edit_esg FROM users").fetchall():
            if user["company_id"] and user["role"] == "company_manager":
                conn.execute("UPDATE companies SET manager_user_id=COALESCE(manager_user_id,?) WHERE id=?", (user["id"], user["company_id"]))
            if user["company_id"] and user["department_id"] is None:
                dept = conn.execute("SELECT id FROM departments WHERE company_id=? AND name='General'", (user["company_id"],)).fetchone()
                if dept:
                    conn.execute("UPDATE users SET department_id=? WHERE id=?", (dept["id"], user["id"]))

        # Move existing source rows into department partitions based on their workbook sheet.
        if _table_exists(conn, "legacy_dataset_rows"):
            for row in conn.execute("SELECT DISTINCT company_id,sheet_name FROM legacy_dataset_rows WHERE department_id IS NULL").fetchall():
                dept_name = _department_name(row["sheet_name"])
                conn.execute("INSERT OR IGNORE INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)", (row["company_id"], dept_name, now(), now()))
                dept = conn.execute("SELECT id FROM departments WHERE company_id=? AND name=? COLLATE NOCASE", (row["company_id"], dept_name)).fetchone()
                conn.execute("UPDATE legacy_dataset_rows SET department_id=? WHERE company_id=? AND sheet_name=? AND department_id IS NULL", (dept["id"], row["company_id"], row["sheet_name"]))
        conn.execute("UPDATE esg_records SET department_id=(SELECT id FROM departments d WHERE d.company_id=esg_records.company_id AND d.name='General') WHERE department_id IS NULL")

        _upgrade_esg_record_constraint(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS records_tenant_dept_year ON esg_records(company_id,department_id,financial_year)")
        conn.execute("CREATE INDEX IF NOT EXISTS legacy_tenant_dept ON legacy_dataset_rows(company_id,department_id,sheet_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS audit_created_at ON audit_logs(created_at)")
        conn.executescript("""
        DROP TRIGGER IF EXISTS esg_department_insert_guard;
        DROP TRIGGER IF EXISTS esg_department_update_guard;
        DROP TRIGGER IF EXISTS legacy_department_insert_guard;
        DROP TRIGGER IF EXISTS legacy_department_update_guard;
        CREATE TRIGGER esg_department_insert_guard BEFORE INSERT ON esg_records
        WHEN NEW.department_id IS NULL OR NOT EXISTS(SELECT 1 FROM departments d WHERE d.id=NEW.department_id AND d.company_id=NEW.company_id)
        BEGIN SELECT RAISE(ABORT,'ESG department must belong to the selected company'); END;
        CREATE TRIGGER esg_department_update_guard BEFORE UPDATE OF company_id,department_id ON esg_records
        WHEN NEW.department_id IS NULL OR NOT EXISTS(SELECT 1 FROM departments d WHERE d.id=NEW.department_id AND d.company_id=NEW.company_id)
        BEGIN SELECT RAISE(ABORT,'ESG department must belong to the selected company'); END;
        CREATE TRIGGER legacy_department_insert_guard BEFORE INSERT ON legacy_dataset_rows
        WHEN NEW.department_id IS NULL OR NOT EXISTS(SELECT 1 FROM departments d WHERE d.id=NEW.department_id AND d.company_id=NEW.company_id)
        BEGIN SELECT RAISE(ABORT,'Imported row department must belong to the selected company'); END;
        CREATE TRIGGER legacy_department_update_guard BEFORE UPDATE OF company_id,department_id ON legacy_dataset_rows
        WHEN NEW.department_id IS NULL OR NOT EXISTS(SELECT 1 FROM departments d WHERE d.id=NEW.department_id AND d.company_id=NEW.company_id)
        BEGIN SELECT RAISE(ABORT,'Imported row department must belong to the selected company'); END;
        """)
        conn.execute("PRAGMA user_version=5")


def _department_name(sheet_name):
    value = str(sheet_name).casefold()
    for needle, name in (("energy", "Energy"), ("water", "Water"), ("waste", "Waste"),
                         ("hr", "People"), ("safety", "Safety"), ("supplier", "Suppliers"),
                         ("procurement", "Procurement")):
        if needle in value:
            return name
    return "Legacy Imported"


def _upgrade_esg_record_constraint(conn):
    definition = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='esg_records'").fetchone()[0] or ""
    if "UNIQUE(company_id,department_id,financial_year)" in definition.replace(" ", ""):
        return
    conn.execute("""CREATE TABLE esg_records_tenant_upgrade(
        id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), department_id INTEGER NOT NULL REFERENCES departments(id),
        financial_year TEXT NOT NULL, energy_kwh REAL NOT NULL CHECK(energy_kwh>=0), water_m3 REAL NOT NULL CHECK(water_m3>=0),
        carbon_tco2e REAL NOT NULL CHECK(carbon_tco2e>=0), waste_tonnes REAL NOT NULL CHECK(waste_tonnes>=0),
        renewable_pct REAL NOT NULL CHECK(renewable_pct BETWEEN 0 AND 100), social_score REAL NOT NULL CHECK(social_score BETWEEN 0 AND 100),
        employee_info TEXT NOT NULL DEFAULT '', governance_info TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
        created_by INTEGER REFERENCES users(id), updated_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(company_id,department_id,financial_year))""")
    conn.execute("""INSERT INTO esg_records_tenant_upgrade(id,company_id,department_id,financial_year,energy_kwh,water_m3,carbon_tco2e,waste_tonnes,renewable_pct,social_score,employee_info,governance_info,source,created_by,updated_by,created_at,updated_at)
        SELECT id,company_id,department_id,financial_year,energy_kwh,water_m3,carbon_tco2e,waste_tonnes,renewable_pct,social_score,employee_info,governance_info,source,created_by,updated_by,created_at,updated_at FROM esg_records""")
    conn.execute("DROP TABLE esg_records")
    conn.execute("ALTER TABLE esg_records_tenant_upgrade RENAME TO esg_records")


def ensure_legacy_company(name, dataset_path, path=None):
    """Register a legacy tenant without assigning it to the Platform Admin."""
    name = str(name).strip()
    with connect(path) as conn:
        row = conn.execute("SELECT id FROM companies WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if not row and dataset_path:
            row = conn.execute("SELECT id FROM companies WHERE dataset_path=? ORDER BY id LIMIT 1", (dataset_path,)).fetchone()
        if not row and dataset_path and _table_exists(conn, "legacy_dataset_rows"):
            row = conn.execute("SELECT company_id AS id FROM legacy_dataset_rows WHERE dataset_path=? ORDER BY id LIMIT 1", (dataset_path,)).fetchone()
        if row:
            conn.execute("UPDATE companies SET dataset_path=COALESCE(dataset_path,?),active=1,updated_at=? WHERE id=?", (dataset_path, now(), row["id"]))
            company_id = row["id"]
        else:
            cur = conn.execute("INSERT INTO companies(name,dataset_path,active,created_at,updated_at) VALUES(?,?,1,?,?)", (name, dataset_path, now(), now()))
            company_id = cur.lastrowid
        conn.execute("INSERT OR IGNORE INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)", (company_id, "General", now(), now()))
        return company_id


def department_for_sheet(company_id, sheet_name, path=None):
    name = _department_name(sheet_name)
    with connect(path) as conn:
        conn.execute("INSERT OR IGNORE INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)", (company_id, name, now(), now()))
        return conn.execute("SELECT id FROM departments WHERE company_id=? AND name=? COLLATE NOCASE", (company_id, name)).fetchone()[0]


def _actor(conn, user):
    user_id = user["id"] if isinstance(user, (dict, sqlite3.Row)) else user
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or row["status"] != "approved":
        raise PermissionError("An approved account is required for this operation.")
    if isinstance(user, dict):
        try:
            supplied_version = user["credential_version"]
        except (KeyError, IndexError):
            supplied_version = None
        if not supplied_version or not hmac.compare_digest(supplied_version, _credential_version(row["password_hash"], row["mfa_secret_enc"])):
            raise PermissionError("This session has expired. Please sign in again.")
        token_hash = hashlib.sha256(str(user.get("auth_session_token", "")).encode()).hexdigest()
        session_row = conn.execute("SELECT credential_version,expires_at FROM auth_sessions WHERE token_hash=? AND user_id=?",
                                   (token_hash, row["id"])).fetchone()
        expected = _credential_version(row["password_hash"], row["mfa_secret_enc"])
        if not session_row or session_row["expires_at"] <= time.time() or not hmac.compare_digest(session_row["credential_version"], expected):
            raise PermissionError("Session expired or revoked. Sign in again.")
    if row["role"] in ("platform_super_admin", "company_manager"):
        if not row["mfa_enabled"]:
            raise PermissionError("Complete MFA enrollment before accessing protected pages.")
        if not isinstance(user, dict) or not user.get("mfa_verified"):
            raise PermissionError("MFA verification is required for this session.")
    return row


def validate_session_user(user, path=None):
    """Reload session identity and reject disabled accounts or inactive tenant memberships."""
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] in ("company_manager", "normal_user"):
            active = conn.execute(
                "SELECT 1 FROM companies WHERE id=? AND active=1", (actor["company_id"],)
            ).fetchone()
            if not active:
                raise PermissionError("This account's company is inactive.")
        session_user = _safe_authenticated_user(
            actor,
            mfa_verified=bool(user.get("mfa_verified")) if isinstance(user, dict) else False,
        )
        # _actor has validated this opaque token against auth_sessions above.
        if isinstance(user, dict):
            session_user["auth_session_token"] = user["auth_session_token"]
        return session_user


def revoke_session(user, path=None):
    """Revoke the current server-side login session on logout."""
    with connect(path) as conn:
        actor = _actor(conn, user)
        token_hash = hashlib.sha256(str(user.get("auth_session_token", "")).encode()).hexdigest()
        deleted = conn.execute("DELETE FROM auth_sessions WHERE token_hash=? AND user_id=?", (token_hash, actor["id"])).rowcount
        if deleted:
            _audit(conn, actor["id"], "session_revoked", "session", actor["id"], "user logout")
        return bool(deleted)


def _audit(conn, actor_id, action, entity_type, entity_id=None, details=""):
    conn.execute("INSERT INTO audit_logs(user_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                  (actor_id, action, entity_type, str(entity_id) if entity_id is not None else None, str(details)[:2000], now()))


def _manager_company(conn, actor, company_id):
    if actor["role"] != "company_manager":
        raise PermissionError("Only a Company Manager can manage company data.")
    company = conn.execute("SELECT * FROM companies WHERE id=? AND active=1 AND manager_user_id=?", (company_id, actor["id"])).fetchone()
    if not company:
        raise PermissionError("This company is outside your management scope.")
    return company


def _department(conn, company_id, department_id, active=True):
    sql = "SELECT d.* FROM departments d JOIN companies c ON c.id=d.company_id WHERE d.id=? AND d.company_id=? AND c.active=1" + (" AND d.active=1" if active else "")
    row = conn.execute(sql, (department_id, company_id)).fetchone()
    if not row:
        raise PermissionError("This department is outside the selected company or is inactive.")
    return row


def _can_read_department(conn, actor, company_id, department_id):
    ids = _accessible_company_ids_for_actor(conn, actor)
    if company_id not in ids:
        return False
    if actor["role"] == "viewer":
        return _department(conn, company_id, department_id) is not None
    if actor["role"] == "company_manager":
        _manager_company(conn, actor, company_id)
        return _department(conn, company_id, department_id) is not None
    return actor["role"] == "normal_user" and actor["company_id"] == company_id and actor["department_id"] == department_id


def create_platform_admin(name, email, password, path=None):
    _validate_person(name, email, password)
    initialize_database(path)
    with connect(path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE role='platform_super_admin'").fetchone():
            raise ValueError("A Platform Super Admin already exists.")
        cur = conn.execute("INSERT INTO users(name,email,password_hash,role,status,created_at,updated_at) VALUES(?,?,?,'platform_super_admin','approved',?,?)",
                           (name.strip(), email.strip().lower(), _password_hash(password), now(), now()))
        _audit(conn, cur.lastrowid, "platform_admin_created", "user", cur.lastrowid)
        return cur.lastrowid


def create_superadmin(name, email, password, path=None):
    """Compatibility name for the platform-only initial administrator setup."""
    return create_platform_admin(name, email, password, path)


def _validate_person(name, email, password):
    if not str(name).strip() or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(email).strip()):
        raise ValueError("Enter a valid name and email address.")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")


def register_company(company_name, manager_name, email, password, industry="", website="", path=None, **profile):
    _validate_person(manager_name, email, password)
    company_name = str(company_name).strip()
    if not company_name:
        raise ValueError("Company name is required.")
    if not profile.get("terms_accepted") or not profile.get("declaration_accepted"):
        raise ValueError("Accept the terms and confirm the accuracy declaration to register.")
    registration_number = str(profile.get("registration_number", "")).strip()
    official_email = str(profile.get("official_email", email)).strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", official_email):
        raise ValueError("Enter a valid official company email address.")
    company_size = str(profile.get("company_size", "")).strip()
    if company_size not in ("Small", "Medium", "Large"):
        raise ValueError("Choose a company size: Small, Medium, or Large.")
    logo_blob = profile.get("logo_blob")
    if logo_blob is not None and (not isinstance(logo_blob, bytes) or len(logo_blob) > 2 * 1024 * 1024):
        raise ValueError("Company logo must be a sanitized image no larger than 2 MB.")
    designation = str(profile.get("designation", "")).strip()
    mobile_number = str(profile.get("mobile_number", "")).strip()
    username = str(profile.get("username", "")).strip()
    if username and not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise ValueError("Username must be 3–40 characters using letters, numbers, dot, dash, or underscore.")
    legal_name = str(profile.get("legal_name", "")).strip()
    company_type = str(profile.get("company_type", "")).strip()
    country = str(profile.get("country", "")).strip()
    if not country:
        raise ValueError("Country is required.")
    state = str(profile.get("state", "")).strip()
    city = str(profile.get("city", "")).strip()
    address = str(profile.get("registered_address", "")).strip()
    postal_code = str(profile.get("postal_code", "")).strip()
    phone = str(profile.get("official_phone", "")).strip()
    reporting_year = str(profile.get("financial_reporting_year", "")).strip()
    if reporting_year and not re.fullmatch(r"(?:19|20|21)\d{2}(?:[-/](?:19|20|21)\d{2})?", reporting_year):
        raise ValueError("Enter a valid reporting year, such as 2025 or 2024-2025.")
    initialize_database(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM companies WHERE name=? COLLATE NOCASE", (company_name,)).fetchone():
            raise ValueError("A company with this name is already registered.")
        if registration_number and conn.execute("SELECT 1 FROM companies WHERE registration_number=? COLLATE NOCASE", (registration_number,)).fetchone():
            raise ValueError("This company registration number is already in use.")
        if conn.execute("SELECT 1 FROM companies WHERE official_email=? COLLATE NOCASE", (official_email,)).fetchone():
            raise ValueError("This official company email is already registered.")
        if conn.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (str(email).strip(),)).fetchone():
            raise ValueError("This manager email already has an account.")
        if username and conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone():
            raise ValueError("This username is already in use.")
        cur = conn.execute("INSERT INTO users(name,email,password_hash,role,status,designation,mobile_number,username,created_at,updated_at) VALUES(?,?,?,'company_manager','approved',?,?,?,?,?)",
                           (manager_name.strip(), email.strip().lower(), _password_hash(password), designation, mobile_number, username, now(), now()))
        user_id = cur.lastrowid
        company = conn.execute("""INSERT INTO companies(
            name,legal_name,registration_number,industry,company_type,company_size,country,state,city,registered_address,
            postal_code,official_email,official_phone,website,logo_blob,financial_reporting_year,
            registration_terms_accepted,registration_declaration_accepted,manager_user_id,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?,?,?)""", (
            company_name, legal_name, registration_number, str(industry).strip(), company_type, company_size,
            country, state, city, address, postal_code, official_email, phone, str(website).strip(), logo_blob,
            reporting_year, user_id, now(), now()))
        company_id = company.lastrowid
        dept = conn.execute("INSERT INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)", (company_id, "General", now(), now()))
        conn.execute("UPDATE users SET company_id=?,department_id=? WHERE id=?", (company_id, dept.lastrowid, user_id))
        _audit(conn, user_id, "company_registered", "company", company_id, company_name)
        return user_id, company_id


def register_user(name, email, password, role="normal_user", company_id=None, department_id=None, path=None):
    role = role.lower().replace(" ", "_")
    if role not in ("normal_user", "viewer"):
        raise ValueError("Public registration is limited to Normal User and Viewer accounts.")
    _validate_person(name, email, password)
    initialize_database(path)
    with connect(path) as conn:
        if role == "normal_user":
            if company_id is None or department_id is None:
                raise ValueError("Normal Users must request access to a company and department.")
            _department(conn, company_id, department_id)
        elif company_id is not None or department_id is not None:
            raise ValueError("Viewer registration is platform-wide and does not select one company.")
        cur = conn.execute("INSERT INTO users(name,email,password_hash,role,status,company_id,department_id,created_at,updated_at) VALUES(?,?,?,?,'pending',?,?,?,?)",
                           (name.strip(), email.strip().lower(), _password_hash(password), role, company_id, department_id, now(), now()))
        _audit(conn, cur.lastrowid, "user_registered", "user", cur.lastrowid, "role=" + role)
        return cur.lastrowid


def _limit_setting(name, default, minimum=1, maximum=86400):
    try:
        return min(max(int(os.getenv(name, default)), minimum), maximum)
    except (TypeError, ValueError):
        return default


def _rate_hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _rate_limited(conn, scope, subject, *, maximum, window_seconds):
    digest = _rate_hash(subject)
    row = conn.execute("SELECT * FROM auth_rate_limits WHERE scope=? AND subject_hash=?", (scope, digest)).fetchone()
    current = time.time()
    if row and row["locked_until"] > current:
        return True
    if row and current - row["window_started"] >= window_seconds:
        conn.execute("DELETE FROM auth_rate_limits WHERE scope=? AND subject_hash=?", (scope, digest))
        return False
    return bool(row and row["attempts"] >= maximum)


def _record_auth_failure(conn, action, email, client_ip):
    if action == "login":
        maximum = _limit_setting("ESG_LOGIN_MAX_ATTEMPTS", 5, 2, 100)
        window = _limit_setting("ESG_LOGIN_WINDOW_SECONDS", 900, 60)
        lockout = _limit_setting("ESG_LOGIN_LOCKOUT_SECONDS", 900, 60)
    elif action.startswith("reset"):
        maximum = _limit_setting("ESG_RESET_MAX_ATTEMPTS", 3, 1, 100)
        window = _limit_setting("ESG_RESET_WINDOW_SECONDS", 3600, 60)
        lockout = _limit_setting("ESG_RESET_LOCKOUT_SECONDS", 3600, 60)
    else:
        maximum = _limit_setting("ESG_MFA_MAX_ATTEMPTS", 5, 2, 100)
        window = _limit_setting("ESG_MFA_WINDOW_SECONDS", 900, 60)
        lockout = _limit_setting("ESG_MFA_LOCKOUT_SECONDS", 900, 60)
    current = time.time()
    subjects = [(action + "_ip", client_ip or "unknown"),
                (action + "_pair", (client_ip or "unknown") + "\0" + str(email).strip().casefold())]
    for scope, subject in subjects:
        digest = _rate_hash(subject)
        row = conn.execute("SELECT * FROM auth_rate_limits WHERE scope=? AND subject_hash=?", (scope, digest)).fetchone()
        if not row or current - row["window_started"] >= window:
            attempts, started = 1, current
        else:
            attempts, started = row["attempts"] + 1, row["window_started"]
        locked_until = current + lockout if attempts >= maximum else 0
        conn.execute("""INSERT INTO auth_rate_limits(scope,subject_hash,window_started,attempts,locked_until)
            VALUES(?,?,?,?,?) ON CONFLICT(scope,subject_hash) DO UPDATE SET
            window_started=excluded.window_started,attempts=excluded.attempts,locked_until=excluded.locked_until""",
            (scope, digest, started, attempts, locked_until))


def _is_rate_limited(conn, action, email, client_ip):
    if action == "login":
        maximum = _limit_setting("ESG_LOGIN_MAX_ATTEMPTS", 5, 2, 100)
        window = _limit_setting("ESG_LOGIN_WINDOW_SECONDS", 900, 60)
    elif action.startswith("reset"):
        maximum = _limit_setting("ESG_RESET_MAX_ATTEMPTS", 3, 1, 100)
        window = _limit_setting("ESG_RESET_WINDOW_SECONDS", 3600, 60)
    else:
        maximum = _limit_setting("ESG_MFA_MAX_ATTEMPTS", 5, 2, 100)
        window = _limit_setting("ESG_MFA_WINDOW_SECONDS", 900, 60)
    ip = client_ip or "unknown"
    return (_rate_limited(conn, action + "_ip", ip, maximum=maximum, window_seconds=window) or
            _rate_limited(conn, action + "_pair", ip + "\0" + str(email).strip().casefold(), maximum=maximum, window_seconds=window))


def _clear_rate_pair(conn, action, email, client_ip, *, release_ip_attempt=True):
    subject = (client_ip or "unknown") + "\0" + str(email).strip().casefold()
    conn.execute("DELETE FROM auth_rate_limits WHERE scope=? AND subject_hash=?", (action + "_pair", _rate_hash(subject)))
    if not release_ip_attempt:
        return
    ip_hash = _rate_hash(client_ip or "unknown")
    row = conn.execute("SELECT attempts FROM auth_rate_limits WHERE scope=? AND subject_hash=?", (action + "_ip", ip_hash)).fetchone()
    if row:
        remaining = max(0, row["attempts"] - 1)
        if remaining == 0:
            conn.execute("DELETE FROM auth_rate_limits WHERE scope=? AND subject_hash=?", (action + "_ip", ip_hash))
        else:
            conn.execute("UPDATE auth_rate_limits SET attempts=?,locked_until=0 WHERE scope=? AND subject_hash=?", (remaining, action + "_ip", ip_hash))


def _mfa_cipher():
    key = os.getenv("ESG_MFA_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("MFA is not configured. Set ESG_MFA_ENCRYPTION_KEY before signing in as an administrator.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("ESG_MFA_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def _totp_code(secret, at=None):
    counter = int((time.time() if at is None else at) // 30)
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def _verify_totp(secret, code, at=None):
    if not str(code or "").strip().isdigit() or len(str(code).strip()) != 6:
        return False
    current = time.time() if at is None else at
    return any(hmac.compare_digest(_totp_code(secret, current + delta * 30), str(code).strip()) for delta in (-1, 0, 1))


def _safe_authenticated_user(row, *, mfa_verified=False):
    result = dict(row)
    password_hash = result.pop("password_hash", "")
    mfa_secret_enc = result.get("mfa_secret_enc")
    result.pop("mfa_secret_enc", None)
    result.pop("mfa_recovery_hashes", None)
    result["credential_version"] = _credential_version(password_hash, mfa_secret_enc)
    result["mfa_verified"] = bool(mfa_verified)
    return result


def _issue_authenticated_session(conn, row, *, mfa_verified=False):
    user = _safe_authenticated_user(row, mfa_verified=mfa_verified)
    conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (time.time(),))
    token = secrets.token_urlsafe(32)
    version = _credential_version(row["password_hash"], row["mfa_secret_enc"])
    conn.execute("INSERT INTO auth_sessions(token_hash,user_id,credential_version,expires_at) VALUES(?,?,?,?)",
                 (hashlib.sha256(token.encode()).hexdigest(), row["id"], version, time.time() + 12 * 60 * 60))
    user["auth_session_token"] = token
    return user


def _start_mfa_enrollment(conn, row):
    cipher = _mfa_cipher()
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO mfa_enrollment_challenges(user_id,token_hash,secret_enc,expires_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET token_hash=excluded.token_hash,secret_enc=excluded.secret_enc,expires_at=excluded.expires_at",
                 (row["id"], hashlib.sha256(token.encode()).hexdigest(), cipher.encrypt(secret.encode()).decode(), time.time() + 600))
    return {"id": row["id"], "email": row["email"], "role": row["role"], "status": row["status"],
            "credential_version": _credential_version(row["password_hash"]), "mfa_enrollment_required": True,
            "mfa_enrollment_token": token}


def mfa_enrollment_uri(user_id, token, path=None):
    with connect(path) as conn:
        row = conn.execute("SELECT c.secret_enc,c.expires_at,u.email,u.role FROM mfa_enrollment_challenges c JOIN users u ON u.id=c.user_id WHERE c.user_id=? AND c.token_hash=?",
                           (user_id, hashlib.sha256(str(token).encode()).hexdigest())).fetchone()
        if not row or row["expires_at"] < time.time() or row["role"] not in ("platform_super_admin", "company_manager"):
            raise PermissionError("MFA enrollment session is invalid or expired.")
        secret = _mfa_cipher().decrypt(row["secret_enc"].encode()).decode()
        from urllib.parse import quote
        label = quote("ESG Analytics Platform:" + row["email"])
        issuer = quote("ESG Analytics Platform")
        return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"


def complete_mfa_enrollment(user_id, token, code, path=None, *, client_ip=None):
    recovery_codes = [secrets.token_urlsafe(12) for _ in range(10)]
    invalid_code = False
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _is_rate_limited(conn, "mfa_enroll", user_id, client_ip):
            raise PermissionError("MFA enrollment attempts are temporarily locked.")
        row = conn.execute("SELECT c.*,u.* FROM mfa_enrollment_challenges c JOIN users u ON u.id=c.user_id WHERE c.user_id=? AND c.token_hash=?",
                           (user_id, hashlib.sha256(str(token).encode()).hexdigest())).fetchone()
        if not row or row["expires_at"] < time.time() or row["role"] not in ("platform_super_admin", "company_manager"):
            raise PermissionError("MFA enrollment session is invalid or expired.")
        secret = _mfa_cipher().decrypt(row["secret_enc"].encode()).decode()
        if not _verify_totp(secret, code):
            _record_auth_failure(conn, "mfa_enroll", user_id, client_ip)
            invalid_code = True
        else:
            hashes = [hashlib.sha256(value.encode()).hexdigest() for value in recovery_codes]
            conn.execute("UPDATE users SET mfa_enabled=1,mfa_secret_enc=?,mfa_recovery_hashes=?,updated_at=? WHERE id=?",
                         (row["secret_enc"], json.dumps(hashes), now(), user_id))
            conn.execute("DELETE FROM mfa_enrollment_challenges WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM auth_rate_limits WHERE scope='mfa_enroll_pair' AND subject_hash=?", (_rate_hash((client_ip or "unknown") + "\0" + str(user_id)),))
            _audit(conn, user_id, "mfa_enabled", "user", user_id)
            updated = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            result = {"user": _issue_authenticated_session(conn, updated, mfa_verified=True), "recovery_codes": recovery_codes}
    if invalid_code:
        raise ValueError("Authenticator code is invalid or expired.")
    return result


def _verify_mfa_code(conn, row, code):
    if not row["mfa_enabled"] or not row["mfa_secret_enc"]:
        return False
    secret = _mfa_cipher().decrypt(row["mfa_secret_enc"].encode()).decode()
    if _verify_totp(secret, code):
        return True
    candidate = hashlib.sha256(str(code or "").strip().encode()).hexdigest()
    hashes = json.loads(row["mfa_recovery_hashes"] or "[]")
    for index, stored in enumerate(hashes):
        if hmac.compare_digest(candidate, stored):
            hashes.pop(index)
            conn.execute("UPDATE users SET mfa_recovery_hashes=? WHERE id=?", (json.dumps(hashes), row["id"]))
            _audit(conn, row["id"], "mfa_recovery_code_used", "user", row["id"])
            return True
    return False


def reset_mfa(user, password, verification_code, path=None, *, client_ip=None):
    invalid_code = False
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] not in ("platform_super_admin", "company_manager") or not _verify_password(password, actor["password_hash"]):
            raise PermissionError("MFA reset requires your current password and administrator account.")
        conn.execute("BEGIN IMMEDIATE")
        if _is_rate_limited(conn, "mfa_reset", actor["email"], client_ip):
            raise PermissionError("MFA reset attempts are temporarily locked.")
        current = conn.execute("SELECT * FROM users WHERE id=?", (actor["id"],)).fetchone()
        if not _verify_mfa_code(conn, current, verification_code):
            _record_auth_failure(conn, "mfa_reset", actor["email"], client_ip)
            invalid_code = True
        else:
            conn.execute("UPDATE users SET mfa_enabled=0,mfa_secret_enc=NULL,mfa_recovery_hashes='[]',updated_at=? WHERE id=?", (now(), actor["id"]))
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (actor["id"],))
            _audit(conn, actor["id"], "mfa_reset", "user", actor["id"])
    if invalid_code:
        raise PermissionError("Enter a valid authenticator or unused recovery code to reset MFA.")


def authenticate(email, password, path=None, *, mfa_code=None, client_ip=None):
    email = str(email).strip()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _is_rate_limited(conn, "login", email, client_ip):
            return None
        # Reserve attempts atomically before expensive password verification so
        # parallel requests cannot all pass a stale counter check.
        _record_auth_failure(conn, "login", email, client_ip)
        row = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
        conn.commit()
        if not row or row["status"] != "approved" or not _verify_password(password, row["password_hash"]):
            _audit(conn, row["id"] if row else None, "login_failed", "user", row["id"] if row else None)
            return None
        if row["role"] in ("platform_super_admin", "company_manager"):
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
            if not current or current["status"] != "approved" or current["password_hash"] != row["password_hash"]:
                conn.rollback()
                return None
            row = current
            if not row["mfa_enabled"]:
                result = _start_mfa_enrollment(conn, row)
                _clear_rate_pair(conn, "login", email, client_ip)
                return result
            if not _verify_mfa_code(conn, row, mfa_code):
                _audit(conn, row["id"], "login_failed", "user", row["id"])
                return None
            _clear_rate_pair(conn, "login", email, client_ip)
            _audit(conn, row["id"], "login_succeeded", "user", row["id"])
            return _issue_authenticated_session(conn, row, mfa_verified=True)
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        if not current or current["status"] != "approved" or current["password_hash"] != row["password_hash"]:
            conn.rollback()
            return None
        _clear_rate_pair(conn, "login", email, client_ip)
        _audit(conn, current["id"], "login_succeeded", "user", current["id"])
        return _issue_authenticated_session(conn, current)


def public_company_list(path=None):
    with connect(path) as conn:
        return [dict(row) for row in conn.execute("SELECT id,name FROM companies WHERE active=1 ORDER BY name")]


def list_public_departments(company_id, path=None):
    with connect(path) as conn:
        return [dict(row) for row in conn.execute("SELECT d.id,d.name FROM departments d JOIN companies c ON c.id=d.company_id WHERE d.company_id=? AND d.active=1 AND c.active=1 ORDER BY d.name", (company_id,))]


def accessible_company_ids(user, path=None, conn=None):
    if conn is None:
        with connect(path) as db:
            return accessible_company_ids(user, conn=db)
    actor = _actor(conn, user)
    return _accessible_company_ids_for_actor(conn, actor)


def _accessible_company_ids_for_actor(conn, actor):
    if actor["role"] == "viewer":
        return [r[0] for r in conn.execute("SELECT id FROM companies WHERE active=1")]
    if actor["role"] == "company_manager":
        return [r[0] for r in conn.execute("SELECT id FROM companies WHERE active=1 AND manager_user_id=?", (actor["id"],))]
    if actor["role"] == "normal_user" and actor["company_id"]:
        ok = conn.execute("SELECT 1 FROM companies WHERE id=? AND active=1", (actor["company_id"],)).fetchone()
        return [actor["company_id"]] if ok else []
    return []


def accessible_department_ids(user, company_id=None, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        companies = _accessible_company_ids_for_actor(conn, actor)
        if company_id is not None and company_id not in companies:
            raise PermissionError("This company is outside your access scope.")
        if actor["role"] == "normal_user":
            return [actor["department_id"]] if actor["department_id"] and (company_id is None or actor["company_id"] == company_id) else []
        sql = "SELECT id FROM departments WHERE active=1"
        params = []
        allowed = [company_id] if company_id is not None else companies
        if not allowed:
            return []
        sql += " AND company_id IN (" + ",".join("?" for _ in allowed) + ")"
        params.extend(allowed)
        return [r[0] for r in conn.execute(sql, params)]


def list_companies(user=None, active_only=True, path=None):
    if user is None:
        return public_company_list(path)
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] == "platform_super_admin":
            # Platform administrators see tenant metadata only, not ESG records.
            clause = " WHERE c.active=1" if active_only else ""
            return [dict(r) for r in conn.execute("SELECT c.id,c.name,c.active,c.manager_user_id,c.created_at,c.updated_at FROM companies c" + clause + " ORDER BY c.name")]
        ids = _accessible_company_ids_for_actor(conn, actor)
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        active = " AND c.active=1" if active_only else ""
        return [dict(r) for r in conn.execute(f"SELECT c.* FROM companies c WHERE c.id IN ({marks}){active} ORDER BY c.name", ids)]


def company_profile(user, company_id=None, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] == "company_manager":
            company_id = actor["company_id"]
            row = conn.execute("""SELECT c.*,u.name AS manager_name,u.email AS manager_email,
                u.designation AS manager_designation,u.mobile_number AS manager_mobile_number,
                u.username AS manager_username
                FROM companies c JOIN users u ON u.id=c.manager_user_id WHERE c.id=? AND c.active=1""", (company_id,)).fetchone()
            return dict(row) if row else None
        if actor["role"] == "viewer" and company_id in _accessible_company_ids_for_actor(conn, actor):
            row = conn.execute("SELECT id,name,industry,website FROM companies WHERE id=? AND active=1", (company_id,)).fetchone()
            return dict(row) if row else None
        raise PermissionError("You cannot view this company profile.")


def update_company_profile(user, company_id, name, industry="", website="", path=None):
    name = str(name).strip()
    if not name:
        raise ValueError("Company name is required.")
    with connect(path) as conn:
        actor = _actor(conn, user)
        _manager_company(conn, actor, company_id)
        conn.execute("UPDATE companies SET name=?,industry=?,website=?,updated_at=? WHERE id=?", (name, industry.strip(), website.strip(), now(), company_id))
        _audit(conn, actor["id"], "company_profile_updated", "company", company_id)


def update_company_registration_profile(user, company_id, fields, path=None):
    """Update only the owning Company's registration profile and manager contact details."""
    allowed_company = {
        "name", "legal_name", "registration_number", "industry", "company_type", "company_size", "country",
        "state", "city", "registered_address", "postal_code", "official_email", "official_phone",
        "website", "financial_reporting_year", "logo_blob",
    }
    allowed_user = {"manager_name", "designation", "mobile_number", "username"}
    if not isinstance(fields, dict) or set(fields) - allowed_company - allowed_user:
        raise ValueError("Unsupported company profile field.")
    if "name" in fields and not str(fields["name"]).strip():
        raise ValueError("Company name is required.")
    if "manager_name" in fields and not str(fields["manager_name"]).strip():
        raise ValueError("Manager name is required.")
    if "company_size" in fields and fields["company_size"] not in ("Small", "Medium", "Large"):
        raise ValueError("Choose a valid company size.")
    if "official_email" in fields and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(fields["official_email"]).strip()):
        raise ValueError("Enter a valid official company email address.")
    if "financial_reporting_year" in fields and fields["financial_reporting_year"] and not re.fullmatch(r"(?:19|20|21)\d{2}(?:[-/](?:19|20|21)\d{2})?", str(fields["financial_reporting_year"]).strip()):
        raise ValueError("Enter a valid reporting year, such as 2025 or 2024-2025.")
    if "username" in fields and fields["username"] and not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", str(fields["username"]).strip()):
        raise ValueError("Username must be 3–40 characters using letters, numbers, dot, dash, or underscore.")
    if "logo_blob" in fields and fields["logo_blob"] is not None and (not isinstance(fields["logo_blob"], bytes) or len(fields["logo_blob"]) > 2 * 1024 * 1024):
        raise ValueError("Company logo must be a sanitized image no larger than 2 MB.")
    with connect(path) as conn:
        actor = _actor(conn, user)
        _manager_company(conn, actor, company_id)
        company_fields = {key: value for key, value in fields.items() if key in allowed_company}
        user_fields = {key: value for key, value in fields.items() if key in allowed_user}
        if company_fields.get("registration_number") and conn.execute("SELECT 1 FROM companies WHERE registration_number=? COLLATE NOCASE AND id<>?", (company_fields["registration_number"], company_id)).fetchone():
            raise ValueError("This company registration number is already in use.")
        if company_fields.get("official_email") and conn.execute("SELECT 1 FROM companies WHERE official_email=? COLLATE NOCASE AND id<>?", (company_fields["official_email"], company_id)).fetchone():
            raise ValueError("This official company email is already registered.")
        if user_fields.get("username") and conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE AND id<>?", (user_fields["username"], actor["id"])).fetchone():
            raise ValueError("This username is already in use.")
        if company_fields:
            assignments = ",".join(f"{key}=?" for key in company_fields)
            conn.execute(f"UPDATE companies SET {assignments},updated_at=? WHERE id=?", (*company_fields.values(), now(), company_id))
        if user_fields:
            if "manager_name" in user_fields:
                user_fields["name"] = user_fields.pop("manager_name")
            assignments = ",".join(f"{key}=?" for key in user_fields)
            conn.execute(f"UPDATE users SET {assignments},updated_at=? WHERE id=?", (*user_fields.values(), now(), actor["id"]))
        _audit(conn, actor["id"], "company_registration_profile_updated", "company", company_id, ",".join(sorted(fields)))


def list_departments(user, company_id=None, active_only=True, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        ids = _accessible_company_ids_for_actor(conn, actor)
        company_id = company_id or (actor["company_id"] if actor["role"] in ("company_manager", "normal_user") else None)
        if company_id is not None and company_id not in ids:
            raise PermissionError("This company is outside your department access scope.")
        if actor["role"] == "normal_user":
            rows = conn.execute("SELECT * FROM departments WHERE id=? AND company_id=?", (actor["department_id"], actor["company_id"]))
        elif company_id:
            rows = conn.execute("SELECT * FROM departments WHERE company_id=?" + (" AND active=1" if active_only else "") + " ORDER BY name", (company_id,))
        else:
            if actor["role"] != "viewer":
                return []
            if not ids:
                return []
            rows = conn.execute("SELECT * FROM departments WHERE active=1 AND company_id IN (" + ",".join("?" for _ in ids) + ") ORDER BY company_id,name", ids)
        return [dict(r) for r in rows]


def create_department(user, company_id, name, path=None):
    name = str(name).strip()
    if not name:
        raise ValueError("Department name is required.")
    with connect(path) as conn:
        actor = _actor(conn, user)
        _manager_company(conn, actor, company_id)
        cur = conn.execute("INSERT INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)", (company_id, name, now(), now()))
        _audit(conn, actor["id"], "department_created", "department", cur.lastrowid)
        return cur.lastrowid


def archive_department(user, company_id, department_id, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        _manager_company(conn, actor, company_id)
        dept = _department(conn, company_id, department_id)
        if dept["name"].casefold() == "general":
            raise ValueError("The General department cannot be archived.")
        if conn.execute("SELECT 1 FROM users WHERE department_id=? AND status<>'rejected' LIMIT 1", (department_id,)).fetchone():
            raise ValueError("Reassign department users before archiving this department.")
        conn.execute("UPDATE departments SET active=0,updated_at=? WHERE id=?", (now(), department_id))
        _audit(conn, actor["id"], "department_archived", "department", department_id)


def list_users(user, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] == "platform_super_admin":
            rows = conn.execute("SELECT id,name,email,role,status,company_id,department_id,can_edit_esg,created_at FROM users ORDER BY created_at DESC")
        elif actor["role"] == "company_manager":
            rows = conn.execute("SELECT id,name,email,role,status,company_id,department_id,can_edit_esg,created_at FROM users WHERE company_id=? ORDER BY created_at DESC", (actor["company_id"],))
        else:
            rows = conn.execute("SELECT id,name,email,role,status,company_id,department_id,can_edit_esg,created_at FROM users WHERE id=?", (actor["id"],))
        return [dict(r) for r in rows]


def approve_user(manager, user_id, approve=True, department_id=None, can_edit=False, role="normal_user", path=None):
    with connect(path) as conn:
        actor = _actor(conn, manager)
        target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise ValueError("User was not found.")
        if actor["role"] == "company_manager":
            _manager_company(conn, actor, actor["company_id"])
            if target["company_id"] != actor["company_id"] or target["role"] != "normal_user":
                raise PermissionError("You can approve only Normal Users requesting access to your company.")
            if approve:
                if department_id is None:
                    raise ValueError("Select a department before approving this employee.")
                _department(conn, actor["company_id"], department_id)
                conn.execute("UPDATE users SET status='approved',department_id=?,can_edit_esg=?,updated_at=? WHERE id=?", (department_id, int(bool(can_edit)), now(), user_id))
            else:
                conn.execute("UPDATE users SET status='rejected',updated_at=? WHERE id=?", (now(), user_id))
        elif actor["role"] == "platform_super_admin":
            if target["role"] != "viewer":
                raise PermissionError("Platform approval is limited to Viewer accounts; company employees are managed by their Company Manager.")
            conn.execute("UPDATE users SET status=?,updated_at=? WHERE id=?", ("approved" if approve else "rejected", now(), user_id))
        else:
            raise PermissionError("Only the relevant Company Manager or Platform Super Admin can approve this account.")
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        _audit(conn, actor["id"], "user_approved" if approve else "user_rejected", "user", user_id)


def assign_user_department(manager, user_id, department_id, can_edit, path=None):
    with connect(path) as conn:
        actor = _actor(conn, manager)
        _manager_company(conn, actor, actor["company_id"])
        target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target or target["role"] != "normal_user" or target["company_id"] != actor["company_id"]:
            raise PermissionError("You can assign departments only to Normal Users in your company.")
        _department(conn, actor["company_id"], department_id)
        conn.execute("UPDATE users SET department_id=?,can_edit_esg=?,updated_at=? WHERE id=?", (department_id, int(bool(can_edit)), now(), user_id))
        _audit(conn, actor["id"], "employee_department_permission_updated", "user", user_id, f"department={department_id};edit={int(bool(can_edit))}")


def assign_company_manager(platform_admin, company_id, user_id, path=None):
    with connect(path) as conn:
        actor = _actor(conn, platform_admin)
        if actor["role"] != "platform_super_admin":
            raise PermissionError("Only the Platform Super Admin can transfer company ownership.")
        user = conn.execute("SELECT * FROM users WHERE id=? AND status IN ('approved','pending')", (user_id,)).fetchone()
        company = conn.execute("SELECT * FROM companies WHERE id=? AND active=1", (company_id,)).fetchone()
        if not user or user["role"] not in ("normal_user", "company_manager") or not company:
            raise ValueError("Select an approved user and active company.")
        if user["role"] == "company_manager" and user["company_id"] != company_id:
            raise ValueError("This manager already owns another company.")
        if user["company_id"] not in (None, company_id):
            raise ValueError("The account already belongs to another company and cannot be moved across tenants.")
        if company["manager_user_id"] and company["manager_user_id"] != user_id:
            raise ValueError("The company already has a manager. Ownership transfer requires the current manager to be released first.")
        conn.execute("UPDATE companies SET manager_user_id=?,updated_at=? WHERE id=?", (user_id, now(), company_id))
        conn.execute("UPDATE users SET role='company_manager',status='approved',company_id=?,department_id=NULL,can_edit_esg=0,updated_at=? WHERE id=?", (company_id, now(), user_id))
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        _audit(conn, actor["id"], "legacy_company_manager_assigned", "company", company_id, f"manager_user_id={user_id}")


def release_company_manager(platform_admin, company_id, path=None):
    with connect(path) as conn:
        actor = _actor(conn, platform_admin)
        if actor["role"] != "platform_super_admin":
            raise PermissionError("Only the Platform Super Admin can release a company manager.")
        company = conn.execute("SELECT manager_user_id FROM companies WHERE id=?", (company_id,)).fetchone()
        if not company or not company["manager_user_id"]:
            return
        manager_id = company["manager_user_id"]
        conn.execute("UPDATE companies SET manager_user_id=NULL,updated_at=? WHERE id=?", (now(), company_id))
        conn.execute("UPDATE users SET role='normal_user',company_id=?,department_id=(SELECT id FROM departments WHERE company_id=? AND name='General'),can_edit_esg=0,updated_at=? WHERE id=?", (company_id, company_id, now(), manager_id))
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (manager_id,))
        _audit(conn, actor["id"], "company_manager_released", "company", company_id)


def company_dataset(user, company_id=None, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] == "company_manager":
            return _manager_company(conn, actor, actor["company_id"])["dataset_path"]
        if actor["role"] == "viewer":
            if company_id not in _accessible_company_ids_for_actor(conn, actor):
                raise PermissionError("This company is outside your access scope.")
            row = conn.execute("SELECT dataset_path FROM companies WHERE id=? AND active=1", (company_id,)).fetchone()
            return row[0] if row else None
        raise PermissionError("Dataset access is not available for this account.")


def _validate_tenant_artifact_path(company_id, value, field="Dataset"):
    """Require persisted workbook paths to remain under their tenant-owned storage roots."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} path is required.")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise PermissionError(f"{field} path must be a tenant-relative path.")
    resolved_root = ROOT.resolve()
    resolved = (resolved_root / candidate).resolve()
    roots = (
        (resolved_root / "data" / "company_datasets" / str(company_id)).resolve(),
        (resolved_root / "data" / "uploads" / str(company_id)).resolve(),
    )
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise PermissionError(f"{field} path is outside this company's storage scope.")
    return resolved.relative_to(resolved_root).as_posix()


def set_company_dataset(manager, company_id, dataset_path, path=None):
    with connect(path) as conn:
        actor = _actor(conn, manager)
        company = _manager_company(conn, actor, company_id)
        safe_path = _validate_tenant_artifact_path(company_id, dataset_path)
        conn.execute("UPDATE companies SET dataset_path=?,updated_at=? WHERE id=?", (safe_path, now(), company_id))
        _audit(conn, actor["id"], "company_dataset_changed", "company", company_id)


def _department_edit_allowed(conn, actor, company_id, department_id):
    if actor["role"] == "company_manager":
        _manager_company(conn, actor, company_id)
        _department(conn, company_id, department_id)
        return
    if actor["role"] == "normal_user" and actor["company_id"] == company_id and actor["department_id"] == department_id and actor["can_edit_esg"]:
        _department(conn, company_id, department_id)
        return
    raise PermissionError("You do not have edit permission for this company and department.")


def _check_cleaning_write_scope(conn, actor, company_id, department_id=None):
    if actor["role"] == "company_manager":
        _manager_company(conn, actor, company_id)
        if department_id is not None:
            _department(conn, company_id, department_id)
        return
    if actor["role"] == "normal_user" and department_id is not None:
        _department_edit_allowed(conn, actor, company_id, department_id)
        return
    raise PermissionError("Cleaning requires Company Manager access or department edit permission.")


def create_verified_cleaning_backup(user, company_id, department_id=None, path=None):
    """Back up the whole SQLite database and verify it before any cleaning write."""
    with connect(path) as conn:
        actor = _actor(conn, user)
        _check_cleaning_write_scope(conn, actor, company_id, department_id)
    backup_root = (Path(path).resolve().parent if path is not None else DB_PATH.resolve().parent) / "backups" / "cleaning"
    backup_root.mkdir(parents=True, exist_ok=True)
    destination = backup_root / ("cleaning_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".sqlite3")
    backup_database(destination, path)
    _verify_database_file(destination)
    with connect(path) as conn:
        actor = _actor(conn, user)
        _check_cleaning_write_scope(conn, actor, company_id, department_id)
        _audit(conn, actor["id"], "cleaning_backup_verified", "company", company_id,
               f"department_id={department_id};backup={destination}")
        cur = conn.execute("INSERT INTO cleaning_backups(company_id,department_id,actor_user_id,backup_path,created_at) VALUES(?,?,?,?,?)",
                           (company_id, department_id, actor["id"], str(destination), now()))
        backup_id = cur.lastrowid
    return {"id": backup_id, "path": str(destination)}


def _validate_cleaning_backup(conn, actor, company_id, department_id, metadata):
    if not isinstance(metadata, dict) or not metadata.get("backup_path") or not metadata.get("backup_id"):
        raise ValueError("A verified database backup is required before recording a cleaning operation.")
    backup_row = conn.execute("SELECT * FROM cleaning_backups WHERE id=? AND company_id=? AND actor_user_id=? AND used_at IS NULL",
                              (metadata["backup_id"], company_id, actor["id"])).fetchone()
    if not backup_row or backup_row["department_id"] != department_id or backup_row["backup_path"] != str(metadata["backup_path"]):
        raise PermissionError("The verified backup token does not match this user, company, department, or operation.")
    _verify_database_file(backup_row["backup_path"])
    return backup_row


def _insert_cleaning_history(conn, actor, company_id, department_id, metadata, backup_verified=False):
    backup_row = _validate_cleaning_backup(conn, actor, company_id, department_id, metadata) if not backup_verified else conn.execute(
        "SELECT * FROM cleaning_backups WHERE id=? AND company_id=? AND actor_user_id=? AND used_at IS NULL",
        (metadata["backup_id"], company_id, actor["id"]),
    ).fetchone()
    if not backup_row or backup_row["department_id"] != department_id or backup_row["backup_path"] != str(metadata["backup_path"]):
        raise PermissionError("The verified backup token does not match this user, company, department, or operation.")
    original_path = metadata.get("original_path", "")
    cleaned_path = metadata.get("cleaned_path", "")
    if original_path:
        original_path = _validate_tenant_artifact_path(company_id, original_path, "Original workbook")
    if cleaned_path:
        cleaned_path = _validate_tenant_artifact_path(company_id, cleaned_path, "Cleaned workbook")
    report = metadata.get("report", {})
    cur = conn.execute("""INSERT INTO cleaning_history(
        company_id,department_id,actor_user_id,source_name,source_hash,operation,
        changed_records,removed_records,skipped_records,flagged_records,backup_path,
        original_path,cleaned_path,report_json,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        company_id, department_id, actor["id"], str(metadata.get("source_name", "upload"))[:255],
        str(metadata.get("source_hash", ""))[:128], str(metadata.get("operation", "cleaned_upload"))[:80],
        int(metadata.get("changed_records", 0)), int(metadata.get("removed_records", 0)),
        int(metadata.get("skipped_records", 0)), int(metadata.get("flagged_records", 0)),
        str(metadata["backup_path"]), str(original_path),
        str(cleaned_path), json.dumps(report, ensure_ascii=False, allow_nan=False)[:200000], now()))
    conn.execute("UPDATE cleaning_backups SET used_at=? WHERE id=? AND used_at IS NULL", (now(), backup_row["id"]))
    _audit(conn, actor["id"], "data_cleaning_completed", "company", company_id,
           f"history_id={cur.lastrowid};department_id={department_id};changed={metadata.get('changed_records',0)};removed={metadata.get('removed_records',0)};flagged={metadata.get('flagged_records',0)}")
    return cur.lastrowid


def commit_company_dataset_cleaning(manager, company_id, dataset_path, metadata, path=None):
    """Link a new immutable cleaned workbook and its history in one DB transaction."""
    with connect(path) as conn:
        actor = _actor(conn, manager)
        _check_cleaning_write_scope(conn, actor, company_id)
        safe_path = _validate_tenant_artifact_path(company_id, dataset_path, "Cleaned workbook")
        _validate_cleaning_backup(conn, actor, company_id, None, metadata)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE companies SET dataset_path=?,updated_at=? WHERE id=?", (safe_path, now(), company_id))
        history_id = _insert_cleaning_history(conn, actor, company_id, None, metadata, backup_verified=True)
        _audit(conn, actor["id"], "company_dataset_changed_after_cleaning", "company", company_id,
               f"history_id={history_id};dataset_path={safe_path}")
        return history_id


def list_cleaning_history(user, company_id=None, department_id=None, limit=100, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] == "company_manager":
            own_company = actor["company_id"]
            if company_id is not None and company_id != own_company:
                raise PermissionError("This company is outside your cleaning history scope.")
            _manager_company(conn, actor, own_company)
            sql = "SELECT h.*,u.name actor_name,u.email actor_email,c.name company_name,d.name department_name FROM cleaning_history h JOIN users u ON u.id=h.actor_user_id JOIN companies c ON c.id=h.company_id LEFT JOIN departments d ON d.id=h.department_id WHERE h.company_id=?"
            params = [own_company]
        elif actor["role"] == "normal_user":
            if company_id not in (None, actor["company_id"]) or department_id not in (None, actor["department_id"]):
                raise PermissionError("This data is outside your cleaning history scope.")
            _department(conn, actor["company_id"], actor["department_id"])
            sql = "SELECT h.*,u.name actor_name,u.email actor_email,c.name company_name,d.name department_name FROM cleaning_history h JOIN users u ON u.id=h.actor_user_id JOIN companies c ON c.id=h.company_id LEFT JOIN departments d ON d.id=h.department_id WHERE h.company_id=? AND h.department_id=?"
            params = [actor["company_id"], actor["department_id"]]
        elif actor["role"] == "viewer":
            allowed = _accessible_company_ids_for_actor(conn, actor)
            if company_id is not None and company_id not in allowed:
                raise PermissionError("This company is outside your cleaning history scope.")
            if not allowed:
                return []
            sql = "SELECT h.*,u.name actor_name,u.email actor_email,c.name company_name,d.name department_name FROM cleaning_history h JOIN users u ON u.id=h.actor_user_id JOIN companies c ON c.id=h.company_id LEFT JOIN departments d ON d.id=h.department_id WHERE h.company_id IN (" + ",".join("?" for _ in allowed) + ")"
            params = list(allowed)
            if company_id is not None:
                sql += " AND h.company_id=?"
                params.append(company_id)
        else:
            raise PermissionError("Platform Admins do not access tenant cleaning history through this service.")
        if department_id is not None:
            sql += " AND h.department_id=?"
            params.append(department_id)
        sql += " ORDER BY h.id DESC LIMIT ?"
        params.append(min(max(int(limit), 1), 1000))
        return [dict(row) for row in conn.execute(sql, params)]


def restore_company_dataset(manager, company_id, history_id, confirmation, path=None):
    """Point the company's active workbook back at an immutable original upload."""
    if confirmation != "RESTORE ORIGINAL":
        raise ValueError("Type RESTORE ORIGINAL to confirm this operation.")
    with connect(path) as conn:
        actor = _actor(conn, manager)
        _manager_company(conn, actor, company_id)
        history = conn.execute("SELECT * FROM cleaning_history WHERE id=? AND company_id=? AND department_id IS NULL", (history_id, company_id)).fetchone()
        if not history or not history["original_path"]:
            raise ValueError("No original workbook is available for this cleaning run.")
        safe_original = _validate_tenant_artifact_path(company_id, history["original_path"], "Original workbook")
        original = (ROOT / safe_original).resolve()
        if not original.is_file():
            raise FileNotFoundError("The preserved original workbook is missing.")
        previous = conn.execute("SELECT dataset_path FROM companies WHERE id=?", (company_id,)).fetchone()[0]
        conn.execute("UPDATE companies SET dataset_path=?,updated_at=? WHERE id=?", (safe_original, now(), company_id))
        _audit(conn, actor["id"], "original_company_workbook_restored", "company", company_id,
               f"history_id={history_id};previous_dataset={previous or ''}")
        return safe_original


def save_esg_record(user, company_id, department_id, financial_year, energy_kwh, water_m3, carbon_tco2e,
                    waste_tonnes, renewable_pct, social_score, employee_info="", governance_info="", record_id=None, path=None):
    year = str(financial_year).strip()
    if not re.fullmatch(r"(?:FY)?20\d{2}(?:[-/](?:20)?\d{2})?", year, re.IGNORECASE):
        raise ValueError("Financial Year must look like FY2025 or 2024-25.")
    values = [float(energy_kwh), float(water_m3), float(carbon_tco2e), float(waste_tonnes), float(renewable_pct), float(social_score)]
    if any(v < 0 for v in values[:4]) or not 0 <= values[4] <= 100 or not 0 <= values[5] <= 100:
        raise ValueError("Measures must be non-negative and percentage/score values must be from 0 to 100.")
    with connect(path) as conn:
        actor = _actor(conn, user)
        _department_edit_allowed(conn, actor, company_id, department_id)
        if record_id:
            old = conn.execute("SELECT * FROM esg_records WHERE id=?", (record_id,)).fetchone()
            if not old or old["company_id"] != company_id or old["department_id"] != department_id:
                raise PermissionError("This ESG record is outside your department scope.")
            conn.execute("UPDATE esg_records SET financial_year=?,energy_kwh=?,water_m3=?,carbon_tco2e=?,waste_tonnes=?,renewable_pct=?,social_score=?,employee_info=?,governance_info=?,updated_by=?,updated_at=? WHERE id=?",
                         (year, *values, employee_info, governance_info, actor["id"], now(), record_id))
            result_id, action = record_id, "esg_record_updated"
        else:
            cur = conn.execute("INSERT INTO esg_records(company_id,department_id,financial_year,energy_kwh,water_m3,carbon_tco2e,waste_tonnes,renewable_pct,social_score,employee_info,governance_info,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (company_id, department_id, year, *values, employee_info, governance_info, actor["id"], actor["id"], now(), now()))
            result_id, action = cur.lastrowid, "esg_record_created"
        _audit(conn, actor["id"], action, "esg_record", result_id)
        return result_id


def list_esg_records(user, company_id=None, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        allowed = _accessible_company_ids_for_actor(conn, actor)
        if company_id is not None and company_id not in allowed:
            raise PermissionError("This company is outside your ESG record access scope.")
        if not allowed:
            return []
        marks = ",".join("?" for _ in allowed)
        sql = f"SELECT r.*,c.name company_name,d.name department_name FROM esg_records r JOIN companies c ON c.id=r.company_id JOIN departments d ON d.id=r.department_id WHERE c.active=1 AND r.company_id IN ({marks})"
        params = list(allowed)
        if actor["role"] == "normal_user":
            sql += " AND r.department_id=?"
            params.append(actor["department_id"])
        if company_id is not None:
            sql += " AND r.company_id=?"
            params.append(company_id)
        sql += " ORDER BY c.name,d.name,r.financial_year DESC"
        return [dict(row) for row in conn.execute(sql, params)]


def list_legacy_rows(user, company_id=None, department_id=None, sheet_name=None, limit=100000, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        allowed = _accessible_company_ids_for_actor(conn, actor)
        if company_id is not None and company_id not in allowed:
            raise PermissionError("This company is outside your legacy data access scope.")
        if not allowed:
            return []
        sql = "SELECT r.*,c.name company_name,d.name department_name FROM legacy_dataset_rows r JOIN companies c ON c.id=r.company_id JOIN departments d ON d.id=r.department_id WHERE c.active=1 AND r.company_id IN (" + ",".join("?" for _ in allowed) + ")"
        params = list(allowed)
        if actor["role"] == "normal_user":
            sql += " AND r.department_id=?"
            params.append(actor["department_id"])
        if department_id is not None:
            dept_row = conn.execute("SELECT company_id FROM departments WHERE id=? AND active=1", (department_id,)).fetchone()
            if not dept_row or not _can_read_department(conn, actor, dept_row["company_id"], department_id):
                raise PermissionError("This department is outside your data access scope.")
            sql += " AND r.department_id=?"
            params.append(department_id)
        if company_id is not None:
            sql += " AND r.company_id=?"
            params.append(company_id)
        if sheet_name:
            sql += " AND r.sheet_name=?"
            params.append(sheet_name)
        sql += " ORDER BY c.name,d.name,r.row_number LIMIT ?"
        params.append(min(max(int(limit), 1), 100000))
        return [dict(row) for row in conn.execute(sql, params)]


def import_department_rows(user, department_id, source_label, source_hash, rows, path=None, cleaning_metadata=None):
    if len(rows) > 50000:
        raise ValueError("A single upload is limited to 50,000 rows.")
    with connect(path) as conn:
        actor = _actor(conn, user)
        if actor["role"] == "company_manager":
            company_id = actor["company_id"]
            _department_edit_allowed(conn, actor, company_id, department_id)
        elif actor["role"] == "normal_user":
            company_id = actor["company_id"]
            _department_edit_allowed(conn, actor, company_id, department_id)
        else:
            raise PermissionError("Only a Company Manager or an authorized department employee can upload department data.")
        dept = _department(conn, company_id, department_id)
        label = "uploads/" + re.sub(r"[^A-Za-z0-9_.-]+", "_", str(source_label))[:160]
        if cleaning_metadata is not None:
            _validate_cleaning_backup(conn, actor, company_id, department_id, cleaning_metadata)
        prepared_rows = []
        for number, row in enumerate(rows, start=2):
            payload = {str(k): _json_safe(v) for k, v in row.items()}
            prepared_rows.append((company_id, department_id, label, source_hash, dept["name"], number, json.dumps(payload, ensure_ascii=False, allow_nan=False), now()))
        conn.execute("BEGIN IMMEDIATE")
        total = conn.executemany("INSERT OR IGNORE INTO legacy_dataset_rows(company_id,department_id,dataset_path,source_hash,sheet_name,row_number,payload_json,imported_at) VALUES(?,?,?,?,?,?,?,?)", prepared_rows).rowcount
        if cleaning_metadata is not None:
            _insert_cleaning_history(conn, actor, company_id, department_id, cleaning_metadata, backup_verified=True)
        _audit(conn, actor["id"], "department_data_uploaded", "department", department_id, f"rows={total};source={label}")
        return total


def _json_safe(value):
    try:
        import pandas as pd
        if value is None or bool(pd.isna(value)):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError, AttributeError):
            pass
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def archive_company(user, company_id, path=None):
    with connect(path) as conn:
        actor = _actor(conn, user)
        _manager_company(conn, actor, company_id)
        conn.execute("UPDATE companies SET active=0,updated_at=? WHERE id=?", (now(), company_id))
        _audit(conn, actor["id"], "company_archived", "company", company_id)


def request_password_reset(email, path=None, *, client_ip=None):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _is_rate_limited(conn, "reset_request", email, client_ip):
            return None
        _record_auth_failure(conn, "reset_request", email, client_ip)
        row = conn.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE AND status='approved'", (str(email).strip(),)).fetchone()
        if row:
            conn.execute("INSERT INTO password_resets(email,token_hash,expires_at) VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET token_hash=excluded.token_hash,expires_at=excluded.expires_at",
                         (email.strip().lower(), hashlib.sha256(token.encode()).hexdigest(), expires))
            _audit(conn, row["id"], "password_reset_requested", "user", row["id"])
    return token if row else None


def issue_password_reset(actor, target_user_id, path=None):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
    with connect(path) as conn:
        caller = _actor(conn, actor)
        target = conn.execute("SELECT * FROM users WHERE id=? AND status='approved'", (target_user_id,)).fetchone()
        if caller["role"] == "company_manager":
            _manager_company(conn, caller, caller["company_id"])
            if not target or target["company_id"] != caller["company_id"] or target["role"] != "normal_user":
                raise PermissionError("You can issue reset tokens only to approved employees in your company.")
        elif caller["role"] != "platform_super_admin":
            raise PermissionError("Platform or Company Manager access is required.")
        if not target:
            raise ValueError("Approved account not found.")
        conn.execute("INSERT INTO password_resets(email,token_hash,expires_at) VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET token_hash=excluded.token_hash,expires_at=excluded.expires_at",
                     (target["email"].lower(), hashlib.sha256(token.encode()).hexdigest(), expires))
        _audit(conn, caller["id"], "password_reset_token_issued", "user", target_user_id)
        return {"email": target["email"], "token": token, "expires_at": expires}


def reset_password(email, token, new_password, path=None, *, client_ip=None):
    if len(new_password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    invalid = False
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _is_rate_limited(conn, "reset_verify", email, client_ip):
            raise ValueError("Reset token is invalid or expired.")
        # Serialize validation and consumption so a token cannot be redeemed twice
        # by requests that both read it before either one deletes it.
        row = conn.execute("SELECT token_hash,expires_at FROM password_resets WHERE email=? COLLATE NOCASE", (email.strip(),)).fetchone()
        if not row or row["expires_at"] < now() or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row["token_hash"]):
            _record_auth_failure(conn, "reset_verify", email, client_ip)
            invalid = True
        else:
            password_hash = _password_hash(new_password)
            _clear_rate_pair(conn, "reset_verify", email, client_ip, release_ip_attempt=False)
            conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE email=? COLLATE NOCASE", (password_hash, now(), email.strip()))
            user_id = conn.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE", (email.strip(),)).fetchone()[0]
            conn.execute("DELETE FROM password_resets WHERE email=? COLLATE NOCASE", (email.strip(),))
            conn.execute("DELETE FROM mfa_enrollment_challenges WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            _audit(conn, user_id, "password_reset_completed", "user", user_id)
    if invalid:
        raise ValueError("Reset token is invalid or expired.")


def update_user_role(actor, user_id, role, path=None):
    if role not in ("normal_user", "viewer"):
        raise ValueError("Company Managers cannot assign privileged platform roles.")
    with connect(path) as conn:
        manager = _actor(conn, actor)
        if manager["role"] != "company_manager":
            raise PermissionError("Only a Company Manager can change a user's role in their company.")
        target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target or target["company_id"] != manager["company_id"] or target["role"] not in ("normal_user", "viewer"):
            raise PermissionError("This user is outside your company administration scope.")
        if role == "viewer":
            raise ValueError("Company Managers cannot grant platform-wide Viewer access.")
        conn.execute("UPDATE users SET role=?,updated_at=? WHERE id=?", (role, now(), user_id))
        _audit(conn, manager["id"], "employee_role_changed", "user", user_id, role)


def audit_logs(actor, limit=500, path=None):
    with connect(path) as conn:
        user = _actor(conn, actor)
        if user["role"] == "platform_super_admin":
            rows = conn.execute("SELECT a.*,u.email FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT ?", (limit,))
        elif user["role"] == "company_manager":
            rows = conn.execute("SELECT a.*,u.email FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id WHERE a.entity_type='company' AND a.entity_id=? OR a.user_id IN (SELECT id FROM users WHERE company_id=?) ORDER BY a.id DESC LIMIT ?",
                                (str(user["company_id"]), user["company_id"], limit))
        else:
            rows = conn.execute("SELECT a.*,u.email FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id WHERE a.user_id=? ORDER BY a.id DESC LIMIT ?", (user["id"], limit))
        return [dict(row) for row in rows]


def migration_status(actor=None, path=None):
    with connect(path) as conn:
        if actor is not None and _actor(conn, actor)["role"] != "platform_super_admin":
            raise PermissionError("Only the Platform Super Admin can view platform migration status.")
        if not _table_exists(conn, "migration_log"):
            return []
        return [dict(row) for row in conn.execute("SELECT * FROM migration_log ORDER BY id DESC")]


def platform_users(actor, path=None):
    with connect(path) as conn:
        if _actor(conn, actor)["role"] != "platform_super_admin":
            raise PermissionError("Platform administration access is required.")
        return [dict(row) for row in conn.execute("SELECT id,name,email,role,status,company_id,department_id,created_at FROM users ORDER BY created_at DESC")]


def platform_company_metadata(actor, path=None):
    with connect(path) as conn:
        if _actor(conn, actor)["role"] != "platform_super_admin":
            raise PermissionError("Platform administration access is required.")
        return [dict(row) for row in conn.execute("SELECT c.id,c.name,c.active,c.manager_user_id,u.name manager_name FROM companies c LEFT JOIN users u ON u.id=c.manager_user_id ORDER BY c.name")]


def initial_platform_admin_exists(path=None):
    with connect(path) as conn:
        return conn.execute("SELECT 1 FROM users WHERE role='platform_super_admin'").fetchone() is not None


def offline_reset_mfa(platform_email, platform_password, target_email, confirmation, path=None):
    """Host-operator recovery for an admin who has lost both MFA and recovery codes."""
    if confirmation != "OFFLINE MFA RESET":
        raise ValueError("Type OFFLINE MFA RESET to confirm this recovery action.")
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (str(platform_email).strip(),)).fetchone()
        if not actor or actor["role"] != "platform_super_admin" or actor["status"] != "approved" or not _verify_password(platform_password, actor["password_hash"]):
            raise PermissionError("Offline MFA recovery requires an approved Platform Super Admin password.")
        target = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (str(target_email).strip(),)).fetchone()
        if not target or target["role"] not in ("platform_super_admin", "company_manager"):
            raise ValueError("An administrator account was not found.")
        conn.execute("UPDATE users SET mfa_enabled=0,mfa_secret_enc=NULL,mfa_recovery_hashes='[]',updated_at=? WHERE id=?", (now(), target["id"]))
        conn.execute("DELETE FROM mfa_enrollment_challenges WHERE user_id=?", (target["id"],))
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (target["id"],))
        _audit(conn, actor["id"], "mfa_offline_recovery", "user", target["id"], "operator-confirmed offline recovery")
        return target["id"]


if __name__ == "__main__":
    import argparse
    import getpass
    parser = argparse.ArgumentParser(description="Initialize the ESG tenant database or create the initial Platform Super Admin.")
    parser.add_argument("command", choices=("init", "create-superadmin", "create-platform-admin", "reset-mfa"))
    args = parser.parse_args()
    initialize_database(allow_create=(args.command == "init"))
    if args.command in ("create-superadmin", "create-platform-admin"):
        create_platform_admin(input("Name: "), input("Email: "), getpass.getpass("Password (12+ characters): "))
        print("Platform Super Admin created.")
    elif args.command == "reset-mfa":
        platform_email = input("Platform Super Admin email: ")
        platform_password = getpass.getpass("Platform Super Admin password: ")
        target_email = input("Administrator account email to reset: ")
        confirmation = input("Type OFFLINE MFA RESET: ")
        offline_reset_mfa(platform_email, platform_password, target_email, confirmation)
        print("MFA reset. The account must enroll an authenticator at next sign-in.")
    else:
        print("Tenant database initialized at", DB_PATH)
