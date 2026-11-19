"""#4708: private one-shot transaction supervisor; no production caller.

No migration, capacity default, GUI command or provider capability. The
trusted Core caller must acquire a separate fresh human confirmation and
an exact review witness, then pass the live source-scope predicate (which
must not acquire Storage's lock or issue SQL). No automatic retry.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from threading import Lock

from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .destructive_cleanup_policy import cleanup_review_epoch, destructive_cleanup_enabled
from .missing_placement_archive_admission import _observe_archive_admission
from .missing_placement_archive_recovery import review_archive_transition
from .missing_placement_archive_sql import _change


def _confirmed_archive_command(service, *, identity_id, library_root,
                               expected_payload, expected_digest,
                               expected_epoch, expected_review,
                               max_records, max_bytes, restore,
                               confirmed=False, scope_current=None):
    """Consume a separately reviewed exact private request once, or refuse.

    Capacity is explicitly supplied; no configured capacity or production
    activation exists. Exact review must be sampled before confirmation.
    """
    if (confirmed is not True or type(restore) is not bool
            or type(expected_review) is not tuple or len(expected_review) != 5
            or expected_review[:3] != (expected_digest, identity_id, str(library_root))
            or not isinstance(library_root, Path) or library_root.anchor != "/"
            or not callable(scope_current) or type(expected_epoch) is not str):
        return None
    config, storage, owner = service.config, service.storage, service.current_identity

    def current():
        try:
            return (service.config is config and service.storage is storage
                    and service.current_identity is owner
                    and owner.identity_id == identity_id
                    and destructive_cleanup_enabled(config)
                    and cleanup_review_epoch(config) == expected_epoch
                    and Path(config.get("library.directory")) == library_root
                    and scope_current() is True)
        except Exception:
            return False

    if not current():
        return None
    mutex = Lock()
    consumed = False
    options = dict(identity_id=identity_id, expected_payload=expected_payload,
                   expected_digest=expected_digest, max_records=max_records,
                   max_bytes=max_bytes)
    initial, final = ("archived", "live") if restore else ("live", "archived")

    def run():
        nonlocal consumed
        with mutex:
            if consumed:
                return "confirmation_consumed"
            consumed = True
        if not current():
            return "blocked"
        commit_attempted = False
        committed = False
        try:
            # Strict existing lease never creates a new lock file on an
            # unavailable/replaced path. Missing lock means refusal.
            with InterProcessLock(acquisition_lock_file(), existing_nofollow=True) as lock:
                def allowed():
                    return current() and lock.strict_current()

                if not allowed():
                    return "blocked"
                with storage._lock:
                    db = storage._connect()
                    if db.in_transaction:
                        return "blocked"
                    previous_sync = int(db.execute("PRAGMA synchronous").fetchone()[0])
                    try:
                        db.execute("PRAGMA synchronous=FULL")
                        db.execute("BEGIN IMMEDIATE")

                        def witness(phase):
                            return _observe_archive_admission(
                                db, library_root=library_root, phase=phase, **options,
                            )

                        if not allowed() or witness(initial) != expected_review or not allowed():
                            return "blocked"
                        _change(
                            db, expected_payload=expected_payload, expected_digest=expected_digest,
                            max_records=max_records, max_bytes=max_bytes,
                            config=config, expected_epoch=expected_epoch, confirmed=True,
                            scope_current=allowed, restore=restore,
                        )
                        if not allowed() or witness(final) != expected_review or not allowed():
                            return "blocked"
                        commit_attempted = True
                        db.commit()
                        committed = True
                    finally:
                        if db.in_transaction:
                            db.rollback()
                        db.execute(f"PRAGMA synchronous={previous_sync}")
                # The separate read snapshot observes durable SQL, never
                # turns an ambiguous commit into permission to replay it.
                if not lock.strict_current():
                    return "commit_unverified"
                evidence = review_archive_transition(
                    storage, explicitly_requested=True, **options,
                )
                wanted = "live_exact_observed" if restore else "archive_exact_observed"
                if committed and evidence["status"] == wanted and lock.strict_current():
                    return "restored" if restore else "archived"
                return "commit_unverified"
        except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError,
                AttributeError, RecursionError):
            return "commit_unverified" if commit_attempted else "blocked"

    return run
