"""Small, testable helpers for the interactive login transition."""

import logging
from pathlib import Path


def _diagnostic_logger(database_path):
    """Return a file logger for browser login diagnostics, with console fallback."""
    resolved_path = Path(database_path).expanduser().resolve()
    logger = logging.getLogger("esg.login." + str(abs(hash(str(resolved_path)))))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            log_path = resolved_path.parent / "logs" / "login_diagnostics.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.StreamHandler())
    return logger


def authenticate_with_diagnostics(email, password, authenticate, database_path, logger=None):
    """Authenticate while logging outcome and DB location, never credentials."""
    logger = logger or _diagnostic_logger(database_path)
    resolved_path = str(Path(database_path).expanduser().resolve())
    logger.info("Login authentication started; database=%s", resolved_path)
    try:
        user = authenticate(email, password)
    except Exception as error:
        safe_detail = str(error).replace(password, "[REDACTED]") if password else str(error)
        logger.error(
            "Login authentication raised %s: %s; database=%s",
            type(error).__name__, safe_detail, resolved_path,
        )
        raise
    outcome = "succeeded" if user is not None else "failed"
    logger.info("Login authentication %s; database=%s", outcome, resolved_path)
    return user


def establish_login_session(session_state, user):
    """Store an authenticated user and reset role navigation after valid login."""
    if user is None:
        return False
    session_state.pop("main_nav", None)
    session_state["authenticated"] = True
    session_state["user"] = user
    return True


def authenticate_and_establish(session_state, email, password, authenticate):
    """Pass submitted credentials through unchanged and establish a session on success."""
    user = authenticate(email, password)
    return establish_login_session(session_state, user)
