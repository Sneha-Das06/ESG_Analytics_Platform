from concurrent.futures import ThreadPoolExecutor
import base64
from threading import Barrier, Event, local
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import database
import pandas as pd
import sqlite3
from src.migrate_legacy import _import_workbook
from src.artifacts import versioned_cleaning_paths


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.auth_env = patch.dict(os.environ, {"ESG_MFA_ENCRYPTION_KEY": database.Fernet.generate_key().decode(),
            "ESG_LOGIN_MAX_ATTEMPTS": "5", "ESG_RESET_MAX_ATTEMPTS": "3"})
        self.auth_env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        database.initialize_database(self.db)
        database.create_platform_admin("Platform", "platform@example.test", "PlatformPassword123!", self.db)
        self.platform = self._authenticate("platform@example.test", "PlatformPassword123!")
        self.manager_a_id, self.company_a = database.register_company("Company A", "Manager A", "manager.a@example.test", "ManagerAPassword123!", path=self.db, company_size="Medium", country="India", official_email="office.a@example.test", terms_accepted=True, declaration_accepted=True)
        self.manager_b_id, self.company_b = database.register_company("Company B", "Manager B", "manager.b@example.test", "ManagerBPassword123!", path=self.db, company_size="Large", country="India", official_email="office.b@example.test", terms_accepted=True, declaration_accepted=True)
        self.manager_a = self._authenticate("manager.a@example.test", "ManagerAPassword123!")
        self.manager_b = self._authenticate("manager.b@example.test", "ManagerBPassword123!")
        self.department_a = database.create_department(self.manager_a, self.company_a, "Environment", self.db)
        self.department_b = database.create_department(self.manager_b, self.company_b, "People", self.db)

    def tearDown(self):
        self.temp.cleanup()
        self.auth_env.stop()

    def _authenticate(self, email, password, *, mfa_code=None):
        user = database.authenticate(email, password, self.db, mfa_code=mfa_code, client_ip="unit-test")
        if user and user.get("mfa_enrollment_required"):
            with database.connect(self.db) as conn:
                encrypted = conn.execute("SELECT secret_enc FROM mfa_enrollment_challenges WHERE user_id=?", (user["id"],)).fetchone()[0]
            secret = database._mfa_cipher().decrypt(encrypted.encode()).decode()
            result = database.complete_mfa_enrollment(user["id"], user["mfa_enrollment_token"], database._totp_code(secret), self.db)
            return result["user"]
        return user

    def _mfa_code(self, user_id):
        with database.connect(self.db) as conn:
            encrypted = conn.execute("SELECT mfa_secret_enc FROM users WHERE id=?", (user_id,)).fetchone()[0]
        secret = database._mfa_cipher().decrypt(encrypted.encode()).decode()
        return database._totp_code(secret)

    def _new_manager_enrollment(self, suffix):
        email = f"enroll.{suffix}@example.test"
        password = "EnrollmentPassword123!"
        _, company_id = database.register_company(
            f"Enrollment {suffix}", f"Enrollment {suffix}", email, password, path=self.db,
            company_size="Small", country="India", official_email=f"enroll.office.{suffix}@example.test",
            terms_accepted=True, declaration_accepted=True,
        )
        challenge = database.authenticate(email, password, self.db, client_ip=f"enroll-{suffix}")
        self.assertTrue(challenge["mfa_enrollment_required"])
        with self.assertRaises(PermissionError):
            database.list_companies(challenge, path=self.db)
        with database.connect(self.db) as conn:
            encrypted = conn.execute("SELECT secret_enc FROM mfa_enrollment_challenges WHERE user_id=?", (challenge["id"],)).fetchone()[0]
            original_password_hash = conn.execute("SELECT password_hash FROM users WHERE id=?", (challenge["id"],)).fetchone()[0]
        secret = database._mfa_cipher().decrypt(encrypted.encode()).decode()
        uri = database.mfa_enrollment_uri(challenge["id"], challenge["mfa_enrollment_token"], self.db)
        self.assertTrue(uri.startswith("otpauth://totp/"))
        completed = database.complete_mfa_enrollment(
            challenge["id"], challenge["mfa_enrollment_token"], database._totp_code(secret), self.db,
        )
        with database.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT password_hash FROM users WHERE id=?", (challenge["id"],)).fetchone()[0], original_password_hash)
        return email, password, company_id, secret, completed

    def test_login_rate_limit_blocks_brute_force_for_window(self):
        with patch.dict(os.environ, {"ESG_LOGIN_MAX_ATTEMPTS": "3", "ESG_LOGIN_LOCKOUT_SECONDS": "60"}):
            for _ in range(3):
                self.assertIsNone(database.authenticate("manager.a@example.test", "wrong-password", self.db, client_ip="203.0.113.9"))
            self.assertIsNone(database.authenticate("manager.a@example.test", "ManagerAPassword123!", self.db,
                                                     mfa_code=self._mfa_code(self.manager_a["id"]), client_ip="203.0.113.9"))
            self.assertIsNotNone(database.authenticate("manager.a@example.test", "ManagerAPassword123!", self.db,
                                                        mfa_code=self._mfa_code(self.manager_a["id"]), client_ip="203.0.113.10"))

    def test_password_reset_request_rate_limit_is_generic_and_temporary(self):
        with patch.dict(os.environ, {"ESG_RESET_MAX_ATTEMPTS": "2", "ESG_RESET_LOCKOUT_SECONDS": "60"}):
            self.assertIsNotNone(database.request_password_reset("manager.a@example.test", self.db, client_ip="203.0.113.20"))
            self.assertIsNone(database.request_password_reset("unknown@example.test", self.db, client_ip="203.0.113.20"))
            self.assertIsNone(database.request_password_reset("manager.a@example.test", self.db, client_ip="203.0.113.20"))

    def test_invalid_password_reset_tokens_are_rate_limited(self):
        client_ip = "203.0.113.30"
        token = database.request_password_reset("manager.a@example.test", self.db, client_ip=client_ip)
        with patch.dict(os.environ, {"ESG_RESET_MAX_ATTEMPTS": "2", "ESG_RESET_LOCKOUT_SECONDS": "60"}):
            for _ in range(2):
                with self.assertRaises(ValueError):
                    database.reset_password("manager.a@example.test", "incorrect-token", "NewPassword123!", self.db,
                                            client_ip=client_ip)
            with self.assertRaises(ValueError):
                database.reset_password("manager.a@example.test", token, "NewPassword123!", self.db,
                                        client_ip=client_ip)

    def test_mfa_totp_enrollment_encryption_and_recovery_codes(self):
        rfc_secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        self.assertEqual(database._totp_code(rfc_secret, at=59), "287082")
        email, password, _, secret, completed = self._new_manager_enrollment("codes")
        self.assertEqual(len(completed["recovery_codes"]), 10)
        self.assertTrue(completed["user"]["mfa_verified"])
        with database.connect(self.db) as conn:
            stored = conn.execute("SELECT mfa_secret_enc,mfa_recovery_hashes,mfa_enabled FROM users WHERE email=?", (email,)).fetchone()
        self.assertEqual(stored["mfa_enabled"], 1)
        self.assertNotEqual(stored["mfa_secret_enc"], secret)
        self.assertNotIn(completed["recovery_codes"][0], stored["mfa_recovery_hashes"])
        self.assertIsNone(database.authenticate(email, password, self.db, mfa_code="000000", client_ip="codes-ip"))
        logged_in = database.authenticate(email, password, self.db, mfa_code=database._totp_code(secret), client_ip="codes-ip")
        self.assertTrue(logged_in["mfa_verified"])
        forged = dict(logged_in)
        forged.pop("auth_session_token")
        with self.assertRaises(PermissionError):
            database.list_companies(forged, path=self.db)
        forged = dict(logged_in, id=self.platform["id"])
        with self.assertRaises(PermissionError):
            database.list_companies(forged, path=self.db)
        recovery = completed["recovery_codes"][0]
        self.assertTrue(database.authenticate(email, password, self.db, mfa_code=recovery, client_ip="codes-ip")["mfa_verified"])
        self.assertIsNone(database.authenticate(email, password, self.db, mfa_code=recovery, client_ip="codes-ip"))
        with self.assertRaises(PermissionError):
            database.reset_mfa(logged_in, password, "invalid", self.db)
        fresh = database.authenticate(email, password, self.db, mfa_code=database._totp_code(secret), client_ip="codes-ip")
        database.reset_mfa(fresh, password, completed["recovery_codes"][1], self.db)
        with self.assertRaises(PermissionError):
            database.list_companies(fresh, path=self.db)
        reenrollment = database.authenticate(email, password, self.db, client_ip="codes-ip")
        self.assertTrue(reenrollment["mfa_enrollment_required"])
        self.assertEqual(reenrollment["role"], "company_manager")

    def test_mfa_enrollment_login_and_streamlit_reruns_preserve_valid_session(self):
        email, password, company_id, secret, enrolled = self._new_manager_enrollment("rerun")
        user = enrolled["user"]
        for _ in range(3):
            user = database.validate_session_user(user, path=self.db)
            self.assertTrue(user["mfa_verified"])
            self.assertTrue(user["credential_version"])
            self.assertTrue(user["auth_session_token"])
            self.assertNotIn("password_hash", user)
            self.assertNotIn("mfa_secret_enc", user)
            self.assertNotIn("mfa_recovery_hashes", user)
            self.assertEqual([row["id"] for row in database.list_companies(user, path=self.db)], [company_id])

        logged_in = database.authenticate(email, password, self.db, mfa_code=database._totp_code(secret), client_ip="rerun-login")
        refreshed = database.validate_session_user(logged_in, path=self.db)
        self.assertEqual([row["id"] for row in database.list_companies(refreshed, path=self.db)], [company_id])

    def test_expired_mfa_session_is_rejected_after_refresh(self):
        _, _, _, _, enrolled = self._new_manager_enrollment("expired")
        user = enrolled["user"]
        with database.connect(self.db) as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=0 WHERE user_id=?", (user["id"],))
        with self.assertRaisesRegex(PermissionError, "(?i)session expired"):
            database.validate_session_user(user, path=self.db)

    def test_normal_and_viewer_sessions_expire_and_logout_revokes_server_token(self):
        viewer_id = database.register_user("Viewer Session", "viewer.session@example.test", "ViewerPassword123!", "viewer", path=self.db)
        database.approve_user(self.platform, viewer_id, True, path=self.db)
        viewer = database.authenticate("viewer.session@example.test", "ViewerPassword123!", self.db, client_ip="viewer-session")
        viewer = database.validate_session_user(viewer, path=self.db)
        self.assertTrue(viewer["auth_session_token"])
        self.assertCountEqual(database.accessible_company_ids(viewer, path=self.db), [self.company_a, self.company_b])
        self.assertTrue(database.revoke_session(viewer, path=self.db))
        with self.assertRaisesRegex(PermissionError, "expired or revoked"):
            database.list_companies(viewer, path=self.db)

        employee_id = database.register_user("Employee Session", "employee.session@example.test", "EmployeePassword123!",
                                             "normal_user", self.company_a, self.department_a, self.db)
        database.approve_user(self.manager_a, employee_id, True, self.department_a, False, path=self.db)
        employee = database.authenticate("employee.session@example.test", "EmployeePassword123!", self.db, client_ip="employee-session")
        with database.connect(self.db) as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=0 WHERE user_id=?", (employee_id,))
        with self.assertRaisesRegex(PermissionError, "expired or revoked"):
            database.validate_session_user(employee, path=self.db)

    def test_offline_mfa_recovery_requires_platform_password_and_confirmation(self):
        email, _, _, _, _ = self._new_manager_enrollment("offline")
        with self.assertRaises(PermissionError):
            database.offline_reset_mfa("platform@example.test", "incorrect", email, "OFFLINE MFA RESET", self.db)
        with self.assertRaises(ValueError):
            database.offline_reset_mfa("platform@example.test", "PlatformPassword123!", email, "NO", self.db)
        target_id = database.offline_reset_mfa("platform@example.test", "PlatformPassword123!", email,
                                               "OFFLINE MFA RESET", self.db)
        self.assertGreater(target_id, 0)
        self.assertTrue(database.authenticate(email, "EnrollmentPassword123!", self.db, client_ip="offline-ip")["mfa_enrollment_required"])

    def test_current_schema_initialization_fast_path_does_not_write(self):
        before = self.db.stat().st_mtime_ns
        database.initialize_database(self.db)
        self.assertEqual(self.db.stat().st_mtime_ns, before)

    def test_explicit_missing_database_fails_closed_and_setup_is_explicit(self):
        missing = Path(self.temp.name) / "not-created" / "configured.sqlite3"
        with patch.object(database, "DB_PATH", missing), patch.object(database, "DB_PATH_EXPLICIT", True):
            with self.assertRaisesRegex(FileNotFoundError, "Refusing to create an empty database"):
                database.initialize_database(missing)
            self.assertFalse(missing.exists())
            self.assertFalse(missing.parent.exists())
            database.initialize_database(missing, allow_create=True)
            self.assertTrue(missing.is_file())

    def test_backup_uses_active_location_and_preserves_schema_objects(self):
        with database.connect(self.db) as conn:
            conn.execute("CREATE INDEX test_storage_idx ON companies(name)")
            conn.execute("CREATE TRIGGER test_storage_trigger AFTER UPDATE OF name ON companies "
                         "BEGIN UPDATE companies SET updated_at=updated_at WHERE id=NEW.id; END")
            conn.execute("PRAGMA user_version=5")
            conn.commit()

        def schema_snapshot(path):
            with database.connect(path) as conn:
                objects = [tuple(row) for row in conn.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE type IN ('table','index','trigger') ORDER BY type,name"
                )]
                tables = [row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )]
                foreign_keys = {
                    table: [tuple(row) for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')]
                    for table in tables
                }
                return (conn.execute("PRAGMA user_version").fetchone()[0], objects, foreign_keys,
                        conn.execute("PRAGMA foreign_key_check").fetchall())

        original_schema = schema_snapshot(self.db)
        backup = database.backup_database(path=self.db)
        self.assertEqual(backup.parent, self.db.parent / "backups")
        self.assertTrue(database._verify_database_file(backup))
        self.assertEqual(schema_snapshot(backup), original_schema)

    def test_backup_discovery_includes_active_and_legacy_roots(self):
        project = Path(self.temp.name) / "project"
        active = self.db.parent / "backups" / "active.sqlite3"
        legacy = project / "data" / "backups" / "legacy.sqlite3"
        active.parent.mkdir(parents=True, exist_ok=True)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        database.backup_database(active, self.db)
        database.backup_database(legacy, self.db)
        with patch.object(database, "ROOT", project):
            found = database.discover_database_backups(self.db)
        self.assertEqual(set(found), {active.resolve(), legacy.resolve()})

    def test_default_cleaning_backup_uses_active_database_backup_folder(self):
        with patch.object(database, "DB_PATH", self.db):
            result = database.create_verified_cleaning_backup(self.manager_a, self.company_a)
        self.assertEqual(Path(result["path"]).parent, self.db.parent / "backups" / "cleaning")
        self.assertTrue(database._verify_database_file(result["path"]))

    def test_concurrent_write_during_upload_row_preparation(self):
        preparation_started = Event()
        continue_preparation = Event()

        class SlowRow(dict):
            def items(self):
                preparation_started.set()
                continue_preparation.wait(timeout=5)
                return super().items()

        rows = [{"value": index} for index in range(500)] + [SlowRow(value=500)]
        real_connect = database.connect

        def short_timeout_connect(path=None):
            conn = real_connect(path)
            conn.execute("PRAGMA busy_timeout=200")
            return conn

        with patch.object(database, "connect", side_effect=short_timeout_connect):
            with ThreadPoolExecutor(max_workers=1) as pool:
                upload = pool.submit(database.import_department_rows, self.manager_a, self.department_a,
                                     "concurrent.csv", "concurrent-hash", rows, self.db)
                self.assertTrue(preparation_started.wait(timeout=5))
                try:
                    record_id = database.save_esg_record(
                        self.manager_a, self.company_a, self.department_a, "FY2025",
                        1, 1, 1, 1, 1, 1, path=self.db,
                    )
                finally:
                    continue_preparation.set()
                self.assertGreater(record_id, 0)
                self.assertEqual(upload.result(timeout=10), 501)
        self.assertEqual(len(database.list_legacy_rows(self.manager_a, self.company_a, limit=1000, path=self.db)), 501)

    def test_cleaning_backup_verification_does_not_hold_write_lock(self):
        backup = database.create_verified_cleaning_backup(self.manager_a, self.company_a, path=self.db)
        metadata = {
            "backup_id": backup["id"], "backup_path": backup["path"],
            "source_name": "source.xlsx", "source_hash": "hash", "operation": "safe_normalize",
            "changed_records": 1,
            "original_path": f"data/company_datasets/{self.company_a}/originals/source.xlsx",
            "cleaned_path": f"data/company_datasets/{self.company_a}/cleaned/cleaned.xlsx",
        }
        verification_started = Event()
        continue_verification = Event()
        real_connect = database.connect
        real_verify = database._verify_database_file

        def short_timeout_connect(path=None):
            conn = real_connect(path)
            conn.execute("PRAGMA busy_timeout=200")
            return conn

        def delayed_verify(path):
            verification_started.set()
            continue_verification.wait(timeout=5)
            return real_verify(path)

        with patch.object(database, "connect", side_effect=short_timeout_connect), patch.object(database, "_verify_database_file", side_effect=delayed_verify):
            with ThreadPoolExecutor(max_workers=1) as pool:
                cleaning = pool.submit(database.commit_company_dataset_cleaning, self.manager_a, self.company_a,
                                       metadata["cleaned_path"], metadata, self.db)
                self.assertTrue(verification_started.wait(timeout=5))
                try:
                    record_id = database.save_esg_record(
                        self.manager_a, self.company_a, self.department_a, "FY2025",
                        1, 1, 1, 1, 1, 1, path=self.db,
                    )
                finally:
                    continue_verification.set()
                self.assertGreater(record_id, 0)
                self.assertGreater(cleaning.result(timeout=10), 0)
        self.assertEqual(database.company_dataset(self.manager_a, self.company_a, path=self.db), metadata["cleaned_path"])

    def test_company_registration_persists_profile_and_links_manager_atomically(self):
        manager_id, company_id = database.register_company(
            "Company C", "Manager C", "manager.c@example.test", "ManagerCPassword123!", path=self.db,
            legal_name="Company C Private Limited", registration_number="CIN-003", industry="Manufacturing",
            company_type="Private Limited", company_size="Small", country="India", state="Karnataka",
            city="Bengaluru", registered_address="1 Green Road", postal_code="560001",
            official_email="contact.c@example.test", official_phone="+91 1234567890",
            website="https://company-c.example.test", financial_reporting_year="2025-2026",
            designation="Sustainability Manager", mobile_number="+91 9876543210", username="manager.c",
            terms_accepted=True, declaration_accepted=True,
        )
        with database.connect(self.db) as conn:
            company = dict(conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone())
            manager = dict(conn.execute("SELECT * FROM users WHERE id=?", (manager_id,)).fetchone())
            audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs WHERE entity_type='company' AND entity_id=? AND action='company_registered'", (str(company_id),)).fetchone()[0]
        self.assertEqual(company["manager_user_id"], manager_id)
        self.assertEqual(company["registration_number"], "CIN-003")
        self.assertEqual(company["registered_address"], "1 Green Road")
        self.assertEqual(company["registration_terms_accepted"], 1)
        self.assertEqual(manager["company_id"], company_id)
        self.assertEqual(manager["role"], "company_manager")
        self.assertEqual(manager["designation"], "Sustainability Manager")
        self.assertNotEqual(manager["password_hash"], "ManagerCPassword123!")
        self.assertEqual(audit_count, 1)
        self.assertEqual(self._authenticate("manager.c@example.test", "ManagerCPassword123!")["company_id"], company_id)
        with self.assertRaises(ValueError):
            database.register_company("Company Duplicate", "Another Manager", "manager.d@example.test", "ManagerDPassword123!", path=self.db, registration_number="cin-003", company_size="Small", country="India", official_email="other@example.test", terms_accepted=True, declaration_accepted=True)
        with self.assertRaises(ValueError):
            database.register_company("Company C", "Another Manager", "manager.e@example.test", "ManagerEPassword123!", path=self.db, company_size="Small", country="India", official_email="other-e@example.test", terms_accepted=True, declaration_accepted=True)
        with self.assertRaises(ValueError):
            database.register_company("Company D", "Another Manager", "manager.c@example.test", "ManagerEPassword123!", path=self.db, company_size="Small", country="India", official_email="other-d@example.test", terms_accepted=True, declaration_accepted=True)
        with database.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE email='manager.d@example.test'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE email='manager.e@example.test'").fetchone()[0], 0)
            conn.execute("CREATE TRIGGER fail_company_insert BEFORE INSERT ON companies WHEN NEW.name='Trigger Failure' BEGIN SELECT RAISE(ABORT,'forced company insert failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            database.register_company("Trigger Failure", "Rollback Manager", "rollback@example.test", "RollbackManagerPassword123!", path=self.db, company_size="Small", country="India", official_email="rollback-office@example.test", terms_accepted=True, declaration_accepted=True)
        with database.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE email='rollback@example.test'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies WHERE name='Trigger Failure'").fetchone()[0], 0)

    def test_cleaning_history_and_backups_are_tenant_scoped_and_verified(self):
        backup = database.create_verified_cleaning_backup(self.manager_a, self.company_a, self.department_a, path=self.db)
        with database.connect(backup["path"]) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 2)
        metadata = {
            "source_name": "sample.csv", "source_hash": "original-hash", "operation": "safe_normalize",
            "backup_id": backup["id"], "backup_path": backup["path"], "original_path": "data/uploads/1/1/originals/sample.csv",
            "cleaned_path": "data/uploads/1/1/cleaned/sample.csv", "changed_records": 1,
            "removed_records": 0, "skipped_records": 0, "flagged_records": 2,
            "report": {"total_records": 3, "valid_records": 1},
        }
        with self.assertRaises(PermissionError):
            database.import_department_rows(
                self.manager_b, self.department_b, "forbidden.csv", "forbidden", [{"value": "x"}],
                path=self.db, cleaning_metadata=metadata,
            )
        database.import_department_rows(
            self.manager_a, self.department_a, "sample_cleaned.csv", "cleaned-hash", [{"value": "clean"}],
            path=self.db, cleaning_metadata=metadata,
        )
        history_a = database.list_cleaning_history(self.manager_a, path=self.db)
        history_b = database.list_cleaning_history(self.manager_b, path=self.db)
        self.assertEqual(len(history_a), 1)
        self.assertEqual(history_b, [])
        self.assertEqual(history_a[0]["removed_records"], 0)
        self.assertEqual(history_a[0]["department_id"], self.department_a)
        with self.assertRaises(PermissionError):
            database.import_department_rows(
                self.manager_a, self.department_a, "replay.csv", "replay", [{"value": "x"}],
                path=self.db, cleaning_metadata=metadata,
            )
        self.assertEqual(len(database.list_legacy_rows(self.manager_a, self.company_a, self.department_a, limit=20, path=self.db)), 1)

    def test_repeated_cleaning_same_filename_creates_separate_history_versions(self):
        base = Path("data/uploads") / str(self.company_a) / str(self.department_a)
        versions = []
        for cleaned_value in ("first", "second"):
            original_rel, cleaned_rel = versioned_cleaning_paths(base, "same-source-hash", "repeated.csv", ".csv")
            backup = database.create_verified_cleaning_backup(self.manager_a, self.company_a, self.department_a, path=self.db)
            metadata = {
                "source_name": "repeated.csv", "source_hash": "same-source-hash", "operation": "safe_normalize",
                "backup_id": backup["id"], "backup_path": backup["path"],
                "original_path": original_rel.as_posix(), "cleaned_path": cleaned_rel.as_posix(),
                "changed_records": 0, "removed_records": 0, "skipped_records": 0, "flagged_records": 0,
                "report": {"version": cleaned_value},
            }
            database.import_department_rows(
                self.manager_a, self.department_a, cleaned_rel.name, cleaned_value,
                [{"version": cleaned_value}], path=self.db, cleaning_metadata=metadata,
            )
            versions.append((original_rel.as_posix(), cleaned_rel.as_posix()))

        history = database.list_cleaning_history(self.manager_a, company_id=self.company_a,
                                                 department_id=self.department_a, path=self.db)
        rows = database.list_legacy_rows(self.manager_a, self.company_a, self.department_a, limit=20, path=self.db)
        self.assertEqual(len(history), 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({entry[0] for entry in versions}), 2)
        self.assertEqual(len({entry[1] for entry in versions}), 2)
        self.assertEqual({row["dataset_path"] for row in rows}, {"uploads/" + Path(version[1]).name for version in versions})
        with self.assertRaises(PermissionError):
            database.create_verified_cleaning_backup(self.manager_a, self.company_b, self.department_b, path=self.db)

    def test_platform_admin_can_restore_only_a_verified_backup_with_confirmation(self):
        database.import_department_rows(self.manager_a, self.department_a, "before.csv", "before", [{"x": 1}], path=self.db)
        backup_path = Path(self.temp.name) / "backups" / "restore_point.sqlite3"
        database.backup_database(backup_path, self.db)
        database.import_department_rows(self.manager_a, self.department_a, "after.csv", "after", [{"x": 2}], path=self.db)
        with self.assertRaises(ValueError):
            database.restore_database(self.platform, backup_path, "", path=self.db)
        with self.assertRaises(PermissionError):
            database.restore_database(self.manager_a, backup_path, "RESTORE DATABASE", path=self.db)
        result = database.restore_database(self.platform, backup_path, "RESTORE DATABASE", path=self.db)
        self.assertTrue(Path(result["pre_restore_backup"]).is_file())
        restored = database.list_legacy_rows(self.manager_a, self.company_a, self.department_a, limit=100, path=self.db)
        self.assertEqual([row["dataset_path"] for row in restored], ["uploads/before.csv"])
        self.assertEqual(database.list_companies(self.manager_a, path=self.db)[0]["id"], self.company_a)

    def test_company_manager_is_confined_to_own_company(self):
        profile = database.company_profile(self.manager_a, path=self.db)
        self.assertEqual(profile["id"], self.company_a)
        database.update_company_profile(self.manager_a, self.company_a, "Company A Updated", "Steel", "https://a.test", self.db)
        with self.assertRaises(PermissionError):
            database.update_company_profile(self.manager_a, self.company_b, "Hacked", path=self.db)
        with self.assertRaises(PermissionError):
            database.update_company_profile(self.manager_b, self.company_a, "Hacked by B", path=self.db)
        with self.assertRaises(PermissionError):
            database.create_department(self.manager_a, self.company_b, "Intrusion", self.db)
        self.assertEqual(database.accessible_company_ids(self.manager_a, self.db), [self.company_a])
        self.assertEqual(database.accessible_company_ids(self.manager_b, self.db), [self.company_b])

    def test_five_company_read_write_matrix_denies_every_cross_tenant_pair(self):
        names = ["Green Future Industries", "JSW Steel", "Microsoft", "Tata Motors", "Tata Steel"]
        tenants = [(self.manager_a, self.company_a, self.department_a), (self.manager_b, self.company_b, self.department_b)]
        for index, name in enumerate(names[2:], start=2):
            email = f"matrix.manager{index}@example.test"
            _, company_id = database.register_company(
                name, f"Manager {index}", email, f"MatrixPassword{index}123!", path=self.db,
                company_size="Medium", country="India", official_email=f"office{index}@example.test",
                terms_accepted=True, declaration_accepted=True,
            )
            manager = self._authenticate(email, f"MatrixPassword{index}123!")
            department_id = database.create_department(manager, company_id, "Environment", self.db)
            tenants.append((manager, company_id, department_id))

        record_ids = []
        for index, (manager, company_id, department_id) in enumerate(tenants):
            record_ids.append(database.save_esg_record(
                manager, company_id, department_id, "FY2025", index + 1, 2, 3, 4, 5, 6, path=self.db,
            ))
            database.import_department_rows(manager, department_id, f"tenant{index}.csv", f"hash{index}",
                                            [{"tenant_marker": names[index]}], path=self.db)
            own_records = database.list_esg_records(manager, company_id, path=self.db)
            own_rows = database.list_legacy_rows(manager, company_id, department_id, limit=10, path=self.db)
            self.assertEqual({row["company_id"] for row in own_records}, {company_id})
            self.assertEqual({row["company_id"] for row in own_rows}, {company_id})

        for source_index, (manager, source_company, _) in enumerate(tenants):
            for target_index, (_, target_company, target_department) in enumerate(tenants):
                if source_index == target_index:
                    continue
                with self.subTest(source=names[source_index], target=names[target_index]):
                    with self.assertRaises(PermissionError):
                        database.list_esg_records(manager, target_company, path=self.db)
                    with self.assertRaises(PermissionError):
                        database.list_legacy_rows(manager, target_company, limit=10, path=self.db)
                    self.assertEqual(database.company_profile(manager, target_company, path=self.db)["id"], source_company)
                    self.assertTrue(all(user["company_id"] == source_company for user in database.list_users(manager, path=self.db)))
                    with self.assertRaises(PermissionError):
                        database.list_departments(manager, target_company, path=self.db)
                    with self.assertRaises(PermissionError):
                        database.save_esg_record(manager, target_company, target_department, "FY2026", 1, 1, 1, 1, 1, 1, path=self.db)
                    with self.assertRaises(PermissionError):
                        database.save_esg_record(manager, tenants[source_index][1], tenants[source_index][2], "FY2026", 1, 1, 1, 1, 1, 1,
                                                 record_id=record_ids[target_index], path=self.db)
                    with self.assertRaises(PermissionError):
                        database.update_company_profile(manager, target_company, "Unauthorized", path=self.db)
                    with self.assertRaises(PermissionError):
                        database.update_company_registration_profile(manager, target_company, {"name": "Unauthorized"}, path=self.db)
                    with self.assertRaises(PermissionError):
                        database.create_department(manager, target_company, "Intrusion", path=self.db)
                    with self.assertRaises(PermissionError):
                        database.archive_department(manager, target_company, target_department, path=self.db)
                    with self.assertRaises(PermissionError):
                        database.archive_company(manager, target_company, path=self.db)
                    with self.assertRaises(PermissionError):
                        database.set_company_dataset(manager, tenants[source_index][1],
                                                     f"data/company_datasets/{target_company}/private.xlsx", path=self.db)

    def test_dataset_and_cleaning_history_paths_cannot_cross_company_storage(self):
        with self.assertRaises(PermissionError):
            database.set_company_dataset(
                self.manager_a, self.company_a,
                f"data/company_datasets/{self.company_b}/private.xlsx", path=self.db,
            )
        with self.assertRaises(PermissionError):
            database.set_company_dataset(self.manager_a, self.company_a, "../Company B/private.xlsx", path=self.db)

        backup = database.create_verified_cleaning_backup(self.manager_a, self.company_a, path=self.db)
        metadata = {
            "backup_id": backup["id"], "backup_path": backup["path"], "source_name": "safe.xlsx",
            "source_hash": "hash", "operation": "safe_normalize",
            "original_path": f"data/company_datasets/{self.company_b}/original.xlsx",
            "cleaned_path": f"data/company_datasets/{self.company_a}/cleaned.xlsx",
        }
        with self.assertRaises(PermissionError):
            database.commit_company_dataset_cleaning(
                self.manager_a, self.company_a, metadata["cleaned_path"], metadata, path=self.db,
            )

    def test_password_reset_token_is_consumed_once_under_concurrent_requests(self):
        email = "manager.a@example.test"
        token = database.request_password_reset(email, path=self.db)
        rendezvous = Barrier(2)
        thread_state = local()
        real_now = database.now

        def synchronize_after_token_read():
            if not getattr(thread_state, "waited", False):
                thread_state.waited = True
                try:
                    rendezvous.wait(timeout=0.5)
                except Exception:
                    pass
            return real_now()

        def redeem(candidate_password):
            try:
                database.reset_password(email, token, candidate_password, path=self.db)
                return candidate_password
            except ValueError:
                return None

        with patch.object(database, "now", side_effect=synchronize_after_token_read):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(redeem, ("FirstResetPassword123!", "SecondResetPassword123!")))

        successful = [password for password in results if password is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(self._authenticate(email, successful[0], mfa_code=self._mfa_code(self.manager_a["id"]))["id"], self.manager_a["id"])
        with self.assertRaises(PermissionError):
            database.list_companies(self.manager_a, path=self.db)
        refreshed = self._authenticate(email, successful[0], mfa_code=self._mfa_code(self.manager_a["id"]))
        self.assertEqual([company["id"] for company in database.list_companies(refreshed, path=self.db)], [self.company_a])

    def test_employee_company_and_department_scope_is_backend_enforced(self):
        employee_id = database.register_user("Employee A", "employee.a@example.test", "EmployeeAPassword123!", "normal_user", self.company_a, self.department_a, self.db)
        with self.assertRaises(PermissionError):
            database.approve_user(self.manager_b, employee_id, True, self.department_b, True, path=self.db)
        database.approve_user(self.manager_a, employee_id, True, self.department_a, True, path=self.db)
        employee = self._authenticate("employee.a@example.test", "EmployeeAPassword123!")
        general = next(d["id"] for d in database.list_departments(self.manager_a,self.company_a,path=self.db) if d["name"] == "General")
        database.save_esg_record(self.manager_a,self.company_a,general,"FY2024",10,10,1,1,10,10,path=self.db)
        self.assertEqual(database.accessible_company_ids(employee, self.db), [self.company_a])
        record_id = database.save_esg_record(employee, self.company_a, self.department_a, "FY2025", 15, 10, 3, 2, 40, 75, path=self.db)
        database.import_department_rows(self.manager_a,general,"other_department.csv","general-hash",[{"secret":"finance only"}],path=self.db)
        self.assertEqual(database.list_legacy_rows(employee,self.company_a,limit=10,path=self.db),[])
        with self.assertRaises(PermissionError):
            database.save_esg_record(employee, self.company_b, self.department_b, "FY2025", 1, 1, 1, 1, 10, 10, path=self.db)
        with self.assertRaises(PermissionError):
            database.save_esg_record(employee, self.company_a, self.department_b, "FY2026", 1, 1, 1, 1, 10, 10, path=self.db)
        self.assertEqual([r["id"] for r in database.list_esg_records(employee, path=self.db)], [record_id])
        with self.assertRaises(PermissionError):
            database.list_esg_records(employee, self.company_b, self.db)
        with self.assertRaises(PermissionError):
            database.list_legacy_rows(employee,self.company_b,limit=10,path=self.db)

    def test_company_manager_can_approve_employees_and_manage_department_records(self):
        employee_id = database.register_user("Employee", "employee@example.test", "EmployeePassword123!", "normal_user", self.company_a, self.department_a, self.db)
        database.approve_user(self.manager_a, employee_id, True, self.department_a, True, path=self.db)
        employee = self._authenticate("employee@example.test", "EmployeePassword123!")
        record = database.save_esg_record(self.manager_a, self.company_a, self.department_a, "FY2025", 100, 20, 8, 4, 30, 85, path=self.db)
        database.save_esg_record(employee, self.company_a, self.department_a, "FY2025", 120, 22, 9, 5, 35, 80, record_id=record, path=self.db)
        self.assertEqual(database.list_esg_records(self.manager_a, self.company_a, self.db)[0]["energy_kwh"], 120)
        self.assertEqual(database.list_users(self.manager_a, self.db)[0]["company_id"], self.company_a)

    def test_approved_viewer_can_read_all_companies_but_never_write(self):
        viewer_id = database.register_user("Auditor", "auditor@example.test", "AuditorPassword123!", "viewer", path=self.db)
        self.assertIsNone(self._authenticate("auditor@example.test", "AuditorPassword123!"))
        database.approve_user(self.platform, viewer_id, True, path=self.db)
        viewer = self._authenticate("auditor@example.test", "AuditorPassword123!")
        self.assertCountEqual(database.accessible_company_ids(viewer, self.db), [self.company_a, self.company_b])
        database.save_esg_record(self.manager_a, self.company_a, self.department_a, "FY2025", 50, 12, 5, 2, 30, 70, path=self.db)
        self.assertEqual(len(database.list_esg_records(viewer, path=self.db)), 1)
        with self.assertRaises(PermissionError):
            database.save_esg_record(viewer, self.company_a, self.department_a, "FY2026", 1, 1, 1, 1, 10, 10, path=self.db)
        with self.assertRaises(PermissionError):
            database.save_esg_record(self.platform, self.company_a, self.department_a, "FY2026", 1, 1, 1, 1, 10, 10, path=self.db)

    def test_request_time_validation_reloads_status_and_blocks_archived_tenant(self):
        manager_session = dict(self.manager_a)
        self.assertEqual(database.validate_session_user(manager_session, self.db)["status"], "approved")
        with database.connect(self.db) as conn:
            conn.execute("UPDATE users SET status='disabled' WHERE id=?", (self.manager_a["id"],))
        with self.assertRaises(PermissionError):
            database.validate_session_user(manager_session, self.db)

        with database.connect(self.db) as conn:
            conn.execute("UPDATE users SET status='approved' WHERE id=?", (self.manager_a["id"],))
        database.archive_company(self.manager_a, self.company_a, path=self.db)
        with self.assertRaises(PermissionError):
            database.validate_session_user(manager_session, self.db)

    def test_viewer_is_backend_read_only_and_scoped_to_active_companies(self):
        viewer_id = database.register_user("Auditor", "auditor.scope@example.test", "AuditorScopePassword123!", "viewer", path=self.db)
        database.approve_user(self.platform, viewer_id, True, path=self.db)
        viewer = self._authenticate("auditor.scope@example.test", "AuditorScopePassword123!")
        database.save_esg_record(self.manager_a, self.company_a, self.department_a, "FY2025", 50, 12, 5, 2, 30, 70, path=self.db)
        database.import_department_rows(self.manager_a, self.department_a, "viewer.csv", "viewer-hash", [{"marker": "safe"}], path=self.db)
        self.assertCountEqual(database.accessible_company_ids(viewer, self.db), [self.company_a, self.company_b])
        self.assertEqual(len(database.list_esg_records(viewer, self.company_a, self.db)), 1)
        self.assertEqual(len(database.list_legacy_rows(viewer, self.company_a, limit=10, path=self.db)), 1)
        with self.assertRaises(PermissionError):
            database.list_esg_records(viewer, 999999, self.db)
        with self.assertRaises(PermissionError):
            database.list_legacy_rows(viewer, company_id=999999, path=self.db)

        with database.connect(self.db) as conn:
            before = (conn.execute("SELECT COUNT(*) FROM esg_records").fetchone()[0], conn.execute("SELECT COUNT(*) FROM legacy_dataset_rows").fetchone()[0])
        forbidden = (
            lambda: database.save_esg_record(viewer, self.company_a, self.department_a, "FY2026", 1, 1, 1, 1, 1, 1, path=self.db),
            lambda: database.import_department_rows(viewer, self.department_a, "blocked.csv", "blocked", [{"x": 1}], path=self.db),
            lambda: database.update_company_profile(viewer, self.company_a, "Changed", path=self.db),
            lambda: database.update_company_registration_profile(viewer, self.company_a, {}, path=self.db),
            lambda: database.create_department(viewer, self.company_a, "Forbidden", path=self.db),
            lambda: database.archive_department(viewer, self.company_a, self.department_a, path=self.db),
            lambda: database.archive_company(viewer, self.company_a, path=self.db),
            lambda: database.set_company_dataset(viewer, self.company_a, "forbidden.xlsx", path=self.db),
            lambda: database.approve_user(viewer, viewer_id, True, path=self.db),
        )
        for operation in forbidden:
            with self.assertRaises(PermissionError):
                operation()
        with database.connect(self.db) as conn:
            after = (conn.execute("SELECT COUNT(*) FROM esg_records").fetchone()[0], conn.execute("SELECT COUNT(*) FROM legacy_dataset_rows").fetchone()[0])
        self.assertEqual(after, before)

        database.archive_company(self.manager_b, self.company_b, path=self.db)
        self.assertEqual(database.accessible_company_ids(viewer, self.db), [self.company_a])
        with self.assertRaises(PermissionError):
            database.list_esg_records(viewer, self.company_b, self.db)

    def test_database_rejects_cross_company_department_foreign_keys(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with database.connect(self.db) as conn:
                conn.execute("INSERT INTO esg_records(company_id,department_id,financial_year,energy_kwh,water_m3,carbon_tco2e,waste_tonnes,renewable_pct,social_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (self.company_a,self.department_b,"FY2025",1,1,1,1,1,1,database.now(),database.now()))

    def test_platform_admin_can_assign_a_pending_legacy_manager(self):
        legacy=database.ensure_legacy_company("Legacy Tenant","legacy.xlsx",self.db)
        general=database.list_public_departments(legacy,self.db)[0]["id"]
        pending=database.register_user("Legacy manager","legacy.manager@example.test","LegacyManagerPassword123!","normal_user",legacy,general,self.db)
        database.assign_company_manager(self.platform,legacy,pending,self.db)
        manager=self._authenticate("legacy.manager@example.test","LegacyManagerPassword123!")
        self.assertEqual(manager["role"],"company_manager")
        self.assertEqual(database.accessible_company_ids(manager,self.db),[legacy])

    def test_legacy_rows_are_department_linked_and_import_idempotently(self):
        frames={"⚡ Energy_Data":pd.DataFrame([{"Energy_kWh":12,"Date":"2025-01-01"}])}
        first=_import_workbook(self.company_a,"legacy.xlsx","sha256-test",frames,self.db)
        second=_import_workbook(self.company_a,"legacy.xlsx","sha256-test",frames,self.db)
        self.assertEqual((first,second),(1,0))
        rows=database.list_legacy_rows(self.manager_a,self.company_a,limit=20,path=self.db)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["department_name"],"Energy")

    def test_upgrade_preserves_legacy_users_and_records(self):
        old = Path(self.temp.name) / "legacy.sqlite3"
        conn=sqlite3.connect(old)
        conn.executescript("""
        CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,dataset_path TEXT);
        CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE permissions(user_id INTEGER NOT NULL,company_id INTEGER NOT NULL,can_edit INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,company_id));
        CREATE TABLE esg_records(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL,financial_year TEXT NOT NULL,energy_kwh REAL NOT NULL,water_m3 REAL NOT NULL,carbon_tco2e REAL NOT NULL,waste_tonnes REAL NOT NULL,renewable_pct REAL NOT NULL,social_score REAL NOT NULL,employee_info TEXT NOT NULL DEFAULT '',governance_info TEXT NOT NULL DEFAULT '',source TEXT NOT NULL DEFAULT 'manual',created_by INTEGER,updated_by INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(company_id,financial_year));
        CREATE TABLE legacy_dataset_rows(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL,dataset_path TEXT NOT NULL,source_hash TEXT NOT NULL,sheet_name TEXT NOT NULL,row_number INTEGER NOT NULL,payload_json TEXT NOT NULL,imported_at TEXT NOT NULL,UNIQUE(company_id,dataset_path,source_hash,sheet_name,row_number));
        CREATE TABLE audit_logs(id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT,details TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        INSERT INTO companies VALUES(1,'Legacy A','legacy.xlsx');
        INSERT INTO users VALUES(1,'Legacy User','legacy@example.test','legacy-hash','normal_user','approved','2024-01-01','2024-01-01');
        INSERT INTO permissions VALUES(1,1,1);
        INSERT INTO esg_records VALUES(1,1,'FY2024',1,2,3,4,5,6,'','', 'manual',1,1,'2024-01-01','2024-01-01');
        INSERT INTO legacy_dataset_rows VALUES(1,1,'legacy.xlsx','hash','Energy_Data',2,'{"energy":1}','2024-01-01');
        PRAGMA user_version=1;
        """)
        conn.commit();conn.close()
        database.initialize_database(old)
        with database.connect(old) as upgraded:
            self.assertEqual(upgraded.execute("SELECT COUNT(*) FROM esg_records").fetchone()[0],1)
            self.assertEqual(upgraded.execute("SELECT COUNT(*) FROM legacy_dataset_rows").fetchone()[0],1)
            self.assertEqual(upgraded.execute("SELECT role FROM users WHERE id=1").fetchone()[0],"normal_user")
            self.assertIsNotNone(upgraded.execute("SELECT department_id FROM esg_records WHERE id=1").fetchone()[0])
            self.assertEqual(upgraded.execute("PRAGMA foreign_key_check").fetchall(),[])
        self.assertTrue((Path(self.temp.name)/"backups").is_dir())


if __name__ == "__main__": unittest.main()
