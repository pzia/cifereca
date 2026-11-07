"""P1 #4681: Core opt-in reading feedback persists, revokes, and stays private.

No real catalogue network, provider, rights ledger, or EPUB metadata scan.
"""
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from cifereca.storage import Storage
from cifereca.services import LibraryService
from cifereca.core.explicit_reading_preferences import (
    change_preference, preference_snapshot,
)


def _set(store, identity, key, value, revision):
    return change_preference(
        store, identity, library_key=key, preference=value,
        expected_revision=revision, user_confirmed=True,
    )


def test_reading_feedback_sqlite_durable_identity_isolated_reversible(tmp_path):
    database = tmp_path / "prefs.sqlite3"
    first = Storage(database)
    try:
        assert preference_snapshot(first, "alice").revision == 0
        a1 = _set(first, "alice", "artifact:sha256:aa", "liked", 0)
        b1 = _set(first, "bob", "artifact:sha256:aa", "not_interested", 0)
        assert a1.preferences == (("artifact:sha256:aa", "liked"),)
        assert b1.preferences == (("artifact:sha256:aa", "not_interested"),)
        assert first.schema_version() == 14
        second = Storage(database)
        try:
            assert preference_snapshot(second, "alice") == a1
            assert preference_snapshot(second, "bob") == b1
            # A stale user action cannot overwrite a newer explicit choice.
            with pytest.raises(ValueError, match="reading_preference_revision_changed"):
                _set(second, "alice", "artifact:sha256:aa", "not_interested", 0)
            a2 = _set(second, "alice", "artifact:sha256:aa", "not_interested", 1)
            assert a2.revision == 2
            assert preference_snapshot(first, "alice") == a2
            a3 = _set(first, "alice", "artifact:sha256:aa", None, 2)
            assert a3.preferences == () and a3.revision == 3
            assert preference_snapshot(second, "bob") == b1
            a4 = change_preference(
                second, "alice", library_key=None, preference=None,
                expected_revision=3, user_confirmed=True, clear_all=True,
            )
            assert a4.revision == 4 and a4.preferences == ()
            with pytest.raises(ValueError, match="reading_preference_revision_changed"):
                _set(first, "alice", "artifact:sha256:aa", "liked", 3)
        finally:
            second.close()
    finally:
        first.close()


@pytest.mark.parametrize(
    "identity,key,value,revision,confirmed,clear,code",
    [
        ("alice", "book", "liked", 0, False, False, "confirmation_required"),
        ("alice", "book", "liked", True, True, False, "confirmation_required"),
        ("alice", " book", "liked", 0, True, False, "target_invalid"),
        ("alice", "book", "borrowed", 0, True, False, "value_invalid"),
        ("alice", "book", "liked", 0, True, True, "target_invalid"),
        ("alice\nprivate", "book", "liked", 0, True, False, "identity_invalid"),
    ],
)
def test_reading_feedback_invalid_inputs_do_not_write(
    tmp_path, identity, key, value, revision, confirmed, clear, code,
):
    storage = Storage(tmp_path / "prefs.sqlite3")
    try:
        with pytest.raises(ValueError, match="reading_preference_" + code):
            change_preference(
                storage, identity, library_key=key, preference=value,
                expected_revision=revision, user_confirmed=confirmed,
                clear_all=clear,
            )
        assert preference_snapshot(storage, "alice").revision == 0
    finally:
        storage.close()


def test_reading_feedback_corruption_fails_closed_and_preserves_private_content(
    tmp_path,
):
    storage = Storage(tmp_path / "prefs.sqlite3")
    try:
        _set(storage, "alice", "book", "liked", 0)
        # A local DB corruption is not silently treated as an empty preference
        # profile, which would permit a stale click to rewrite it.
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE metadata SET value=? WHERE key LIKE 'reading.preference.v1:%'",
                ('{"schema":1,"revision":1,"preferences":{"book":"untrusted"}}',),
            )
        with pytest.raises(ValueError, match="reading_preference_state_invalid"):
            preference_snapshot(storage, "alice")
        with pytest.raises(ValueError, match="reading_preference_state_invalid"):
            _set(storage, "alice", "book", "liked", 1)
    finally:
        storage.close()


def _host(storage):
    active = SimpleNamespace(identity_id="alice")
    rows = [
        {"key": "artifact:verified", "identity_id": "alice"},
        {"key": "artifact:foreign", "identity_id": "bob"},
    ]
    calls = []
    def entries(**options):
        calls.append(options)
        return list(rows)
    host = SimpleNamespace(
        current_identity=active, storage=storage,
        _search_generation_lock=RLock(), library_entries=entries,
    )
    return host, rows, calls


def test_core_service_explicit_writer_checks_active_owner_exact_key(tmp_path):
    storage = Storage(tmp_path / "prefs.sqlite3")
    try:
        host, rows, calls = _host(storage)
        def write(**kw):
            return LibraryService.change_reading_preference(host, **kw)
        with pytest.raises(ValueError, match="reading_preference_identity_not_active"):
            write(identity_id="bob", library_key="artifact:verified",
                  preference="liked", expected_revision=0, user_confirmed=True)
        with pytest.raises(ValueError, match="reading_preference_confirmation_required"):
            write(identity_id="alice", library_key="artifact:verified",
                  preference="liked", expected_revision=0)
        with pytest.raises(ValueError, match="reading_preference_target_unverified"):
            write(identity_id="alice", library_key="artifact:foreign",
                  preference="liked", expected_revision=0, user_confirmed=True)
        assert LibraryService.reading_preference_snapshot(host, identity_id="alice").revision == 0
        assert calls == [{"identity_id": "alice", "scan": False}]
        a1 = write(
            identity_id="alice", library_key="artifact:verified",
            preference="liked", expected_revision=0, user_confirmed=True,
        )
        assert a1.preferences == (("artifact:verified", "liked"),)
        rows.append({"key": "artifact:verified", "identity_id": "alice"})
        with pytest.raises(ValueError, match="reading_preference_target_unverified"):
            write(identity_id="alice", library_key="artifact:verified",
                  preference="not_interested", expected_revision=1,
                  user_confirmed=True)
        rows.clear()
        # Deletion must remain available if the original book disappeared.
        a2 = write(
            identity_id="alice", library_key="artifact:verified",
            preference=None, expected_revision=1, user_confirmed=True,
        )
        assert a2.preferences == () and a2.revision == 2
        host.current_identity = SimpleNamespace(identity_id="bob")
        with pytest.raises(ValueError, match="reading_preference_identity_not_active"):
            LibraryService.reading_preference_snapshot(host, identity_id="alice")
    finally:
        storage.close()
