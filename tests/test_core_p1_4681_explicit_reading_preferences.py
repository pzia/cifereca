"""P1 #4681: durable explicit user reading feedback is opt-in and scoped.

Core Storage is real SQLite; there is no supplier network or simulated entitlement.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

import pytest

from cifereca.core.explicit_reading_preferences import (
    change_preference,
    preference_snapshot,
)
from cifereca.storage import Storage


@pytest.fixture
def preference_store(tmp_path):
    store = Storage(tmp_path / "preferences.sqlite")
    try:
        yield store
    finally:
        store.close()


def _change(store, identity, *, key=None, preference=None,
            revision=0, confirmed=True, clear_all=False):
    return change_preference(
        store, identity, library_key=key,
        preference=preference, expected_revision=revision,
        user_confirmed=confirmed, clear_all=clear_all,
    )


def test_exact_identity_and_library_key_are_not_inferred_from_shared_catalogues(
    preference_store,
):
    store = preference_store
    assert preference_snapshot(store, "alice").preferences == ()
    assert preference_snapshot(store, "bob").preferences == ()
    first = _change(store, "alice", key="artifact:one",
                    preference="liked", revision=0)
    assert first.revision == 1
    assert first.preferences == (("artifact:one", "liked"),)
    assert preference_snapshot(store, "bob").revision == 0
    assert preference_snapshot(store, "bob").preferences == ()
    # A different profile may have the same library key, but never shares
    # the preference, and the exact source must not be a join key.
    bob = _change(store, "bob", key="artifact:one",
                  preference="not_interested", revision=0)
    assert bob.preferences == (("artifact:one", "not_interested"),)
    assert preference_snapshot(store, "alice") == first

    # Toggle A -> B -> A without a cache, then restart SQLite entirely.
    store.close()
    reopened = Storage(store.path)
    try:
        assert preference_snapshot(reopened, "alice") == first
        assert preference_snapshot(reopened, "bob") == bob
    finally:
        reopened.close()


def test_explicit_confirmation_and_strict_revision_required(preference_store):
    store = preference_store
    for confirmed in (False, None, 0, "true"):
        with pytest.raises(ValueError, match="confirmation_required"):
            _change(store, "alice", key="artifact:one", preference="liked",
                    revision=0, confirmed=confirmed)
    with pytest.raises(ValueError, match="confirmation_required"):
        _change(store, "alice", key="artifact:one", preference="liked",
                revision=True)
    assert preference_snapshot(store, "alice").revision == 0

    first = _change(store, "alice", key="artifact:one",
                    preference="liked", revision=0)
    for stale in (0,):
        with pytest.raises(ValueError, match="revision_changed"):
            _change(store, "alice", key="artifact:one",
                    preference="not_interested", revision=stale)
    assert preference_snapshot(store, "alice") == first


def test_clear_all_removes_feedback_without_resetting_anti_aba_epoch(
    preference_store,
):
    store = preference_store
    liked = _change(store, "alice", key="artifact:one",
                    preference="liked", revision=0)
    second = _change(store, "alice", key="artifact:two",
                     preference="not_interested", revision=liked.revision)
    assert len(second.preferences) == 2
    purged = _change(store, "alice", revision=second.revision, clear_all=True)
    assert purged.preferences == ()
    assert purged.revision == second.revision + 1
    with pytest.raises(ValueError, match="revision_changed"):
        _change(store, "alice", key="artifact:one",
                preference="liked", revision=liked.revision)
    new = _change(store, "alice", key="artifact:one",
                  preference="not_interested", revision=purged.revision)
    assert new.preferences == (("artifact:one", "not_interested"),)

    # Revoking a key also bumps revision, including when the key was absent.
    removed = _change(store, "alice", key="artifact:one",
                      preference=None, revision=new.revision)
    assert removed.preferences == ()
    again = _change(store, "alice", key="artifact:one",
                    preference=None, revision=removed.revision)
    assert again.revision == removed.revision + 1


@pytest.mark.parametrize("identity,key,pref,revision,clear_all", [
    ("", "artifact:one", "liked", 0, False),
    ("alice\nprivate", "artifact:one", "liked", 0, False),
    ("alice", "", "liked", 0, False),
    ("alice", " artifact:one ", "liked", 0, False),
    ("alice", "artifact:\ntoken", "liked", 0, False),
    ("alice", "artifact:one", "read", 0, False),
    ("alice", "artifact:one", True, 0, False),
    ("alice", "artifact:one", "liked", -1, False),
    ("alice", "artifact:one", "liked", 2**63, False),
    ("alice", "artifact:one", "liked", 0, True),
    ("alice", None, "liked", 0, True),
    ("alice", None, None, 0, "true"),
])
def test_malformed_or_ambiguous_mutations_fail_before_any_write(
    preference_store, identity, key, pref, revision, clear_all,
):
    with pytest.raises(ValueError, match="reading_preference_"):
        _change(preference_store, identity, key=key, preference=pref,
                revision=revision, clear_all=clear_all)
    assert preference_snapshot(preference_store, "alice").revision == 0


def test_corrupt_sqlite_payload_is_not_interpreted_as_user_consent(preference_store):
    store = preference_store
    first = _change(store, "alice", key="artifact:one",
                    preference="liked", revision=0)
    with store._lock, store._connect() as db:
        key = db.execute(
            "SELECT key FROM metadata WHERE key LIKE 'reading.preference.v1:%'"
        ).fetchone()[0]
        assert "alice" not in key
        db.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (json.dumps({
                "schema": 1, "revision": first.revision,
                "preferences": {"artifact:one": "provider_inferred"},
            }), key),
        )
    with pytest.raises(ValueError, match="state_invalid"):
        preference_snapshot(store, "alice")
    with pytest.raises(ValueError, match="state_invalid"):
        _change(store, "alice", key="artifact:one",
                preference="not_interested", revision=first.revision)


def test_revision_serializes_two_core_writer_instances(tmp_path):
    path = tmp_path / "race.sqlite"
    a, b = Storage(path), Storage(path)
    gate = Barrier(2)

    def mutate(store, preference):
        gate.wait(timeout=5)
        try:
            return _change(store, "alice", key="artifact:one",
                           preference=preference, revision=0)
        except ValueError as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            x = pool.submit(mutate, a, "liked")
            y = pool.submit(mutate, b, "not_interested")
            outcomes = (x.result(timeout=10), y.result(timeout=10))
        successes = [row for row in outcomes if hasattr(row, "preferences")]
        failures = [row for row in outcomes if isinstance(row, str)]
        assert len(successes) == len(failures) == 1
        assert failures == ["reading_preference_revision_changed"]
        assert preference_snapshot(a, "alice") == successes[0]
        assert preference_snapshot(b, "alice") == successes[0]
    finally:
        a.close()
        b.close()


def test_capacity_refusal_is_atomic_and_writes_no_rights(preference_store):
    store = preference_store
    # Pre-populate the profile with the largest admissible number of keys.
    old = {
        f"artifact:{index:03d}": "liked"
        for index in range(256)
    }
    value = json.dumps({
        "schema": 1, "revision": 1, "preferences": old,
    })
    from cifereca.core.explicit_reading_preferences import _identity_key
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (_identity_key("alice"), value),
        )
    with pytest.raises(ValueError, match="capacity_exceeded"):
        _change(store, "alice", key="artifact:extra",
                preference="liked", revision=1)
    state = preference_snapshot(store, "alice")
    assert state.revision == 1
    assert len(state.preferences) == 256
    # User feedback is descriptive data; these tables are never mutated.
    with store._lock, store._connect() as db:
        assert db.execute("SELECT count(*) FROM backup_items").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM entitlements").fetchone()[0] == 0
