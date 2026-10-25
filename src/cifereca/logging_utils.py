from __future__ import annotations
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from .paths import log_file


_diagnostic_level = "normal"

# The persistent log and its console mirror carry the ORIGINAL event values.
# Only the Diagnostics GTK presentation may pseudonymize login/password.
# Keep the filter class for compatibility with installed file handlers.


def _ensure_trace_console(logger: logging.Logger, *, force: bool = False) -> None:
    """Mirror the same filtered records to an attached terminal, at most once."""
    if any(getattr(handler, "_cifereca_trace_console", False) for handler in logger.handlers):
        return
    if not force and not getattr(sys.stderr, "isatty", lambda: False)():
        return
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    console.addFilter(RedactionFilter())
    console._cifereca_trace_console = True
    logger.addHandler(console)


def set_diagnostics_level(level):
    """Switch the effective file/console logger verbosity without restart."""
    global _diagnostic_level
    level = "verbose" if level == "detailed" else level
    if level not in {"errors", "normal", "verbose", "trace"}:
        raise ValueError("Niveau de diagnostic inconnu")
    _diagnostic_level = level
    logger = logging.getLogger("cifereca")
    logger.setLevel(logging.DEBUG if level in {"verbose", "trace"} else logging.INFO)
    if level == "trace" and logger.handlers:
        _ensure_trace_console(logger)


class RedactionFilter(logging.Filter):
    """Select severity/trace without rewriting records or exception details.

    This compatibility name used to redact non-credential fields, even from
    the local authoritative log. The GUI owns any display pseudonymization.
    Do not mutate a LogRecord shared by the file and console handlers.
    """
    def filter(self, record):
        if _diagnostic_level == "errors" and record.levelno < logging.WARNING:
            return False
        message = record.getMessage()
        from .core.diagnostic_log_policy import is_public_core_observation
        if message.startswith(("TRACE ", "EVENT ")) and is_public_core_observation(record.name, message):
            return True
        if message.startswith("TRACE") and _diagnostic_level != "trace":
            return False
        return True


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Create and rotate a local raw journal readable only by its owner."""
    def _open(self):
        # Creating with a mode argument avoids a transient world-readable
        # file on common umasks. Tighten existing files as well.
        fd = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(fd)
            raise


def configure_logging(*, verbose: bool = False) -> logging.Logger:
    if verbose: set_diagnostics_level("trace")
    logger = logging.getLogger("cifereca")
    if logger.handlers:
        if verbose:
            logger.setLevel(logging.DEBUG)
            _ensure_trace_console(logger, force=True)
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = PrivateRotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
    handler.addFilter(RedactionFilter())
    logger.addHandler(handler)
    if verbose:
        _ensure_trace_console(logger, force=True)
    return logger
