from __future__ import annotations
import logging
import sys
from logging.handlers import RotatingFileHandler
from .paths import log_file


_diagnostic_level = "normal"


def set_diagnostics_level(level):
    global _diagnostic_level
    _diagnostic_level = level
    logging.getLogger("cifereca").setLevel(logging.DEBUG if level in {"verbose", "detailed", "trace"} else logging.INFO)


class RedactionFilter(logging.Filter):
    def filter(self, record):
        from .diagnostics import redact_text, redact
        if _diagnostic_level == "errors" and record.levelno < logging.WARNING:
            return False
        if record.getMessage().startswith("TRACE") and _diagnostic_level != "trace":
            return False
        record.args = redact(record.args) if isinstance(record.args, dict) else tuple(redact(value) for value in (record.args or ()))
        record.msg = redact_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            import traceback
            record.exc_text = redact_text("".join(traceback.format_exception(*record.exc_info)))
        return True


def configure_logging(*, verbose: bool = False) -> logging.Logger:
    if verbose: set_diagnostics_level("trace")
    logger = logging.getLogger("cifereca")
    if logger.handlers:
        if verbose:
            logger.setLevel(logging.DEBUG)
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
    handler.addFilter(RedactionFilter())
    logger.addHandler(handler)
    if verbose:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.DEBUG)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        console.addFilter(RedactionFilter())
        logger.addHandler(console)
    return logger
