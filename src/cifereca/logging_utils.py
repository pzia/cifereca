from __future__ import annotations
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from .paths import log_file


_diagnostic_level = "normal"

# Last-resort protection for *local* unstructured logs. The generic
# diagnostics.redact() contract is shared with the book UI and deliberately
# preserves book titles and some identifiers. Local logs must not retain
# borrower or account references, even when a plugin formats them as text.
# Do not mask provider_id, site or public book_id.
_PRIVATE_LOG_IDENTIFIERS = re.compile(
    r"""(?i)\b(?P<name>loan_id|account_key|account_id|source_account_id|"""
    r"""identity_id|credential_id|profile_id|reader_id|user_id|username|uid|"""
    r"""identity|credential|profile|account|borrower|source_credential|source_item_id|"""
    r"""email|account_name|profile_name|reader_name|user_name|full_name|"""
    r"""api_key|client_secret|private_key|access_key|secret_key|"""
    r"""access_token|refresh_token|id_token|session_id|session_key)\b"""
    r"""['"]?\s*[:=]\s*(?:"(?:\\[^\r\n]|[^"\\\r\n])*(?:"|(?=\r|\n|$))"""
    r"""|'(?:\\[^\r\n]|[^'\\\r\n])*(?:'|(?=\r|\n|$))|[^\s,;}'"]+)"""
)
_LOG_URL = re.compile(r"https?://[^\s<>\"']+", re.I)


def _redact_log_identifiers(value: str) -> str:
    return _PRIVATE_LOG_IDENTIFIERS.sub(
        lambda match: match.group("name") + "=<redacted>", value,
    )


def _redact_local_log_text(value: str) -> str:
    from .diagnostics import redact_text
    # A hostname/path is not inherently public: legacy URL text may contain
    # user-specific subdomains or opaque delivery secrets without a query.
    # Trusted producers publish site separately through their closed schema.
    return _redact_log_identifiers(redact_text(_LOG_URL.sub("<redacted-url>", value)))


def set_diagnostics_level(level):
    global _diagnostic_level
    _diagnostic_level = level
    logging.getLogger("cifereca").setLevel(logging.DEBUG if level in {"verbose", "detailed", "trace"} else logging.INFO)


class RedactionFilter(logging.Filter):
    def filter(self, record):
        from .diagnostics import redact
        if _diagnostic_level == "errors" and record.levelno < logging.WARNING:
            return False
        message = record.getMessage()
        from .core.diagnostic_log_policy import is_public_core_observation
        if message.startswith("TRACE") and is_public_core_observation(record.name, message):
            # A fully validated body is already public. Keep its canonical
            # serialization intact instead of rewriting/truncating arguments.
            # The validation does not attest any attached traceback/stack.
            record.msg, record.args = message, ()
            record.exc_info = record.exc_text = record.stack_info = None
            return True
        if message.startswith("TRACE") and _diagnostic_level != "trace":
            return False
        record.args = redact(record.args) if isinstance(record.args, dict) else tuple(redact(value) for value in (record.args or ()))
        record.msg = _redact_local_log_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            import traceback
            record.exc_text = _redact_local_log_text("".join(traceback.format_exception(*record.exc_info)))
        elif record.exc_text:
            record.exc_text = _redact_local_log_text(record.exc_text)
        if record.stack_info:
            record.stack_info = _redact_local_log_text(record.stack_info)
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
