import unittest
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory

from src.login_flow import authenticate_and_establish, authenticate_with_diagnostics


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append((message, args))

    def exception(self, message, *args):
        self.messages.append((message, args, traceback.format_exc()))

    def error(self, message, *args):
        self.messages.append((message, args, traceback.format_exc()))


class LoginFlowTests(unittest.TestCase):
    def test_success_passes_credentials_unchanged_and_resets_navigation(self):
        session = {"authenticated": False, "main_nav": "Data Quality"}
        user = {"id": 2, "company_id": 6, "role": "company_manager", "status": "approved"}
        received = []

        def authenticate(email, password):
            received.append((email, password))
            return user

        result = authenticate_and_establish(session, " sneha.demo@example.com ", " secret with spaces ", authenticate)

        self.assertTrue(result)
        self.assertEqual(received, [(" sneha.demo@example.com ", " secret with spaces ")])
        self.assertTrue(session["authenticated"])
        self.assertEqual(session["user"], user)
        self.assertNotIn("main_nav", session)

    def test_failed_authentication_does_not_mutate_session(self):
        session = {"authenticated": False, "main_nav": "Login"}
        before = session.copy()

        result = authenticate_and_establish(session, "user@example.test", "bad-password", lambda *_: None)

        self.assertFalse(result)
        self.assertEqual(session, before)

    def test_diagnostics_record_path_and_outcome_without_credentials(self):
        with TemporaryDirectory() as temp:
            logger = RecordingLogger()
            password = "NeverLogThisPassword123!"
            user = authenticate_with_diagnostics(
                "sneha.demo@example.com", password, lambda *_: {"id": 2}, Path(temp) / "db.sqlite3", logger
            )

        logged = repr(logger.messages)
        self.assertEqual(user, {"id": 2})
        expected_path = str((Path(temp) / "db.sqlite3").resolve())
        self.assertTrue(any(expected_path in args for _, args in logger.messages))
        self.assertTrue(any("succeeded" in args for _, args in logger.messages))
        self.assertNotIn(password, logged)

    def test_diagnostics_log_exception_without_credentials_and_reraise(self):
        logger = RecordingLogger()
        password = "NeverLogThisResetTokenOrPassword"

        def broken_auth(*_):
            raise RuntimeError("database unavailable")

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            authenticate_with_diagnostics("sneha.demo@example.com", password, broken_auth, "login.sqlite3", logger)

        logged = repr(logger.messages)
        self.assertIn("database unavailable", logged)
        self.assertIn("login.sqlite3", logged)
        self.assertNotIn(password, logged)


if __name__ == "__main__":
    unittest.main()
