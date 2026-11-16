import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from streamlit.runtime.media_file_manager import MediaFileManager
from streamlit.testing.v1 import AppTest


class SessionSecurityAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "session-security.sqlite3"
        self.env_patch = patch.dict(os.environ, {"ESG_DATABASE_PATH": str(self.db)})
        self.env_patch.start()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.db
        database.initialize_database(self.db, allow_create=True)
        self._seed_account()

    def tearDown(self):
        for name, logger in logging.Logger.manager.loggerDict.items():
            if name.startswith("esg.login.") and isinstance(logger, logging.Logger):
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
        database.DB_PATH = self.previous_db_path
        self.env_patch.stop()
        self.temp.cleanup()

    def _seed_account(self):
        timestamp = database.now()
        with database.connect(self.db) as conn:
            self.company_id = conn.execute(
                "INSERT INTO companies(name,active,created_at,updated_at) VALUES(?,?,?,?)",
                ("Session Test Company", 1, timestamp, timestamp),
            ).lastrowid
            self.department_id = conn.execute(
                "INSERT INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                (self.company_id, "Operations", timestamp, timestamp),
            ).lastrowid
            self.finance_id = conn.execute(
                "INSERT INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                (self.company_id, "Finance", timestamp, timestamp),
            ).lastrowid
            self.other_company_id = conn.execute(
                "INSERT INTO companies(name,active,created_at,updated_at) VALUES(?,?,?,?)",
                ("Other Test Company", 1, timestamp, timestamp),
            ).lastrowid
            self.other_department_id = conn.execute(
                "INSERT INTO departments(company_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                (self.other_company_id, "Operations", timestamp, timestamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO users(name,email,password_hash,role,status,company_id,department_id,
                                     created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    "Session Test User", "session.user@example.test",
                    database._password_hash("SessionTestPassword2026!"),
                    "normal_user", "approved", self.company_id, self.department_id,
                    timestamp, timestamp,
                ),
            )
            rows = (
                (self.company_id, self.department_id, "uploads/operations.csv", "ops-hash",
                 "Operations", '{"marker":"AUTHORIZED_DEPARTMENT_ROW"}'),
                (self.company_id, self.finance_id, "uploads/finance.csv", "finance-hash",
                 "Finance", '{"marker":"OTHER_DEPARTMENT_SECRET"}'),
                (self.other_company_id, self.other_department_id, "uploads/other.csv", "other-hash",
                 "Operations", '{"marker":"OTHER_COMPANY_SECRET"}'),
            )
            for company_id, department_id, label, source_hash, sheet, payload in rows:
                conn.execute(
                    """INSERT INTO legacy_dataset_rows(company_id,department_id,dataset_path,source_hash,
                                                       sheet_name,row_number,payload_json,imported_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (company_id, department_id, label, source_hash, sheet, 2, payload, timestamp),
                )

    def _login(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
        self.assertEqual(database.DB_PATH.resolve(), self.db.resolve())
        next(item for item in app.text_input if item.key == "login_email").set_value(
            "session.user@example.test"
        )
        next(item for item in app.text_input if item.key == "login_password").set_value(
            "SessionTestPassword2026!"
        )
        next(item for item in app.button if item.label == "LOGIN").click().run()
        self.assertTrue(app.session_state["authenticated"])
        self.assertEqual(app.session_state["user"]["company_id"], self.company_id)
        self.assertEqual(app.session_state["user"]["department_id"], self.department_id)
        return app

    def test_disabled_account_during_session_redirects_to_login(self):
        app = self._login()
        with database.connect(self.db) as conn:
            conn.execute("UPDATE users SET status='disabled' WHERE email=?",
                         ("session.user@example.test",))

        app.run()

        self.assertFalse(app.session_state["authenticated"])
        self.assertNotIn("user", app.session_state)
        self.assertNotIn("main_nav", app.session_state)
        self.assertTrue(any("no longer active" in item.value for item in app.error))
        self.assertTrue(any(item.key == "login_email" for item in app.text_input))
        self.assertTrue(any(item.key == "login_password" for item in app.text_input))

    def test_archived_company_during_session_redirects_to_login(self):
        app = self._login()
        with database.connect(self.db) as conn:
            conn.execute("UPDATE companies SET active=0 WHERE id=?", (self.company_id,))

        app.run()

        self.assertFalse(app.session_state["authenticated"])
        self.assertTrue(any("no longer active" in item.value for item in app.error))
        self.assertTrue(any(item.key == "login_email" for item in app.text_input))

    def test_reports_csv_download_payload_is_company_and_department_scoped(self):
        app = self._login()
        reports = next(value for value in app.radio[0].options if "Reports" in value)
        app.radio[0].set_value(reports).run()
        self.assertFalse(app.exception)
        captured = []
        original_add = MediaFileManager.add

        def capture_download(manager, path_or_data, mimetype, coordinates,
                             file_name=None, is_for_static_download=False):
            if file_name == "authorized_source_data.csv":
                captured.append(bytes(path_or_data))
            return original_add(
                manager, path_or_data, mimetype, coordinates, file_name,
                is_for_static_download,
            )

        with patch.object(MediaFileManager, "add", capture_download):
            app.run()

        self.assertEqual(len(captured), 1)
        payload = captured[0].decode("utf-8")
        self.assertIn("AUTHORIZED_DEPARTMENT_ROW", payload)
        self.assertNotIn("OTHER_DEPARTMENT_SECRET", payload)
        self.assertNotIn("OTHER_COMPANY_SECRET", payload)


if __name__ == "__main__":
    unittest.main()