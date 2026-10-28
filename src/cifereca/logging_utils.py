from __future__ import annotations
import errno
import json
import logging
import os
import stat
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
    console.setFormatter(CanonicalEventFormatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    console.addFilter(RedactionFilter())
    console._cifereca_trace_console = True
    logger.addHandler(console)



def _share_canonical_handlers(core: logging.Logger) -> None:
    """Compatibility for older installed Numilog provider packages.

    New Numilog versions use 'cifereca.plugin.provider.numilog' and propagate
    to the canonical Core logger directly. Older 'cifereca_numilog' instances
    reuse the exact file/console handlers, never creating a second journal.
    """
    numilog = logging.getLogger("cifereca_numilog")
    numilog.setLevel(core.level)
    numilog.propagate = False
    # This legacy logger must MIRROR the Core's current canonical handlers,
    # not accumulate retired file/console handlers after a reconfiguration.
    # Keeping a stale file handler writes the same event to two physical
    # journals, and can resurrect a closed/old journal after switching paths.
    for handler in tuple(numilog.handlers):
        if handler not in core.handlers:
            numilog.removeHandler(handler)
    for handler in core.handlers:
        if handler not in numilog.handlers:
            numilog.addHandler(handler)


def diagnostics_level() -> str:
    """Return the sole effective diagnostics level (including HTTP Trace)."""
    return _diagnostic_level


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
    _share_canonical_handlers(logger)


class RedactionFilter(logging.Filter):
    """Select severity/trace without rewriting records or exception details.

    This compatibility name used to redact non-credential fields, even from
    the local authoritative log. The GUI owns any display pseudonymization.
    Do not mutate a LogRecord shared by the file and console handlers.
    """
    def filter(self, record):
        if _diagnostic_level == "errors" and record.levelno < logging.WARNING:
            return False
        from .core.diagnostic_acquisition_failure import is_acquisition_failure
        if is_acquisition_failure(record):
            return True  # An observed failure is not optional verbose TRACE.
        message = record.getMessage()
        from .core.diagnostic_log_policy import is_public_core_observation
        if message.startswith(("TRACE ", "EVENT ")) and is_public_core_observation(record.name, message):
            return True
        if message.startswith("TRACE") and _diagnostic_level != "trace":
            return False
        return True


class CanonicalEventFormatter(logging.Formatter):
    """Serialize typed Core facts inside their original log occurrence.

    Core producers attach _cifereca_journal_payload to a LogRecord. File and
    console share canonical JSON while independent handlers receive the
    unchanged legacy message. No extra emission occurs. The #3632 adapter
    protects credential values in recognized acquisition failures only.
    """

    def format(self, record):
        payload = getattr(record, "_cifereca_journal_payload", None)
        from .core.diagnostic_acquisition_failure import failure_payload
        failure = failure_payload(record)
        if failure is not None:
            payload = failure
        if type(payload) is not dict:
            return super().format(record)
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":"))
        except (TypeError, ValueError, OverflowError):
            # Invalid optional metadata must never hide an actual error.
            return super().format(record)
        clone = logging.makeLogRecord(record.__dict__.copy())
        clone.msg = "DIAG_JSON " + body
        clone.args = ()
        clone.exc_text = None
        if failure is not None:
            # The sanitized cause/chain already belongs to this same record;
            # never append the original credential-bearing exception again.
            clone.exc_info = None
            clone.stack_info = None
            if failure["status"] in {"failed", "refused"} and clone.levelno < logging.ERROR:
                # Same severity contract as the corresponding typed terminal;
                # retain the emitter's original level in observed_severity.
                clone.levelno = logging.ERROR
                clone.levelname = "ERROR"
        return super().format(clone)


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Create and rotate a local raw journal readable only by its owner."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self._secure_retained_rotations()
        except BaseException:
            self.close()
            raise

    def _secure_retained_rotations(self):
        """Tighten legacy backups, including copies created before raw logs.

        Never follow a symbolic link: a retained filename may point outside
        Cifereca's state directory and must not affect that other file.
        """
        if not hasattr(os, "O_NOFOLLOW"):
            return
        for index in range(1, self.backupCount + 1):
            name = f"{self.baseFilename}.{index}"
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    continue  # Do not change an external symlink target.
                raise
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    os.fchmod(fd, 0o600)
            finally:
                os.close(fd)

    def _open(self):
        # Creating with a mode argument avoids a transient world-readable
        # file on common umasks. Tighten existing files as well.
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.baseFilename, flags, 0o600)
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
        _share_canonical_handlers(logger)
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = PrivateRotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(CanonicalEventFormatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
    handler.addFilter(RedactionFilter())
    logger.addHandler(handler)
    if verbose:
        _ensure_trace_console(logger, force=True)
    _share_canonical_handlers(logger)
    return logger
