"""#4708: qualify a private archive proposal without enabling retirement."""
import pytest

from cifereca.core import missing_placement_archive_review as archive
from cifereca.storage import Storage
from tests.test_core_p0_4708_historical_missing_report import _setup


@pytest.fixture
def rig(tmp_path):
    database, _config, root, ids = _setup(tmp_path)
    storage = Storage(database)
    try:
        yield storage, root, ids
    finally:
        storage.close()


def _review(rig, name, identity="a"):
    store, root, ids = rig
    return archive.review_missing_archive_candidate(
        store, library_root=root, identity_id=identity, placement_id=ids[name],
    )


@pytest.mark.parametrize("name,identity,expected", [
    ("unlinked.epub", "a", "supervised_archive_candidate"),
    ("returned.epub", "a", "preserve"),
    ("linked.epub", "a", "preserve"),
    ("unknown.epub", "a", "preserve"),
    ("foreign.epub", "a", "preserve"),
])
def test_private_proposal_is_never_a_retirement_token(rig, name, identity, expected):
    store, root, ids = rig
    before = store.placements()
    report = _review(rig, name, identity)
    assert report["proposal"] == expected
    assert report["archive_authorized"] is False
    assert report["retirement_authorized"] is False
    assert report["requires_fresh_confirmation"] is True
    assert "plan_token" not in report
    assert all(identifier not in repr(report) for identifier in ids.values())
    assert str(root) not in repr(report)
    assert store.placements() == before


def test_reappearing_leaf_revokes_proposal(rig, monkeypatch):
    store, root, ids = rig
    original = archive._extra_metadata_clear

    def reappear(*args, **kwargs):
        result = original(*args, **kwargs)
        (root / "a" / "unlinked.epub").write_bytes(b"returned")
        return result

    monkeypatch.setattr(archive, "_extra_metadata_clear", reappear)
    report = _review(rig, "unlinked.epub")
    assert report["proposal"] == "preserve"
    assert (root / "a" / "unlinked.epub").read_bytes() == b"returned"
    assert len(store.placements()) == len(ids)


def test_symlink_root_refuses_even_if_it_points_at_same_directory(rig):
    store, root, ids = rig
    alias = root.with_name("alias")
    alias.symlink_to(root, target_is_directory=True)
    report = archive.review_missing_archive_candidate(
        store, library_root=alias, identity_id="a",
        placement_id=ids["unlinked.epub"],
    )
    assert report["proposal"] == "preserve"
    assert len(store.placements()) == len(ids)


def test_archive_review_does_not_finish_callers_transaction(rig):
    store, root, ids = rig
    db = store._connect()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                   ("test.archive.uncommitted", "private"))
        result = _review(rig, "unlinked.epub")
        assert result["proposal"] == "preserve"
        assert result["reason"] == "nested_transaction"
        assert db.in_transaction
    finally:
        db.rollback()
    assert store.metadata_get("test.archive.uncommitted") is None


def _operator(rig, *, complete=True, valid=lambda: True):
    from types import SimpleNamespace
    from cifereca.core.missing_placement_operator import MissingPlacementOperatorSession

    store, root, ids = rig
    service = SimpleNamespace(
        storage=store, current_identity=SimpleNamespace(identity_id="a"),
        config=SimpleNamespace(get=lambda key, default=None: str(root)
                               if key == "library.directory" else default),
        identity_by_id=store.identity_get,
    )
    return service, MissingPlacementOperatorSession(
        service, identity_id="a", scope_valid=valid, inspection_complete=complete,
    )


def test_operator_archive_review_consumes_old_retirement_preview_without_new_token(rig):
    store, root, ids = rig
    service, operator = _operator(rig)
    pid = ids["unlinked.epub"]
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    result = operator.review_archive_candidate(placement_id=pid)
    assert result["proposal"] == "supervised_archive_candidate"
    assert not result["retirement_authorized"] and not result["archive_authorized"]
    assert operator.admit_confirmation(user_confirmed=True) is None
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert store.placement_get(pid)["state"] == "missing"


def test_operator_archive_review_rejects_partial_and_changed_scope(rig, monkeypatch):
    from cifereca.core import missing_placement_operator as session
    store, root, ids = rig
    pid = ids["unlinked.epub"]
    _, partial = _operator(rig, complete=False)
    assert partial.review_archive_candidate(placement_id=pid)["reason"] == "incomplete_inspection"
    service, operator = _operator(rig)
    original = session.review_missing_archive_candidate

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        service.current_identity.identity_id = "b"
        return result

    monkeypatch.setattr(session, "review_missing_archive_candidate", changed)
    result = operator.review_archive_candidate(placement_id=pid)
    assert result["proposal"] == "preserve" and result["reason"] == "scope_changed"
    assert operator.admit_confirmation(user_confirmed=True) is None
    assert store.placement_get(pid)["state"] == "missing"


@pytest.mark.parametrize("source", ["metadata_key", "event_json_key", "device_artifact",
                                      "device_path", "malformed_event", "budget"])
def test_additional_reference_evidence_vetoes_archive_proposal(rig, monkeypatch, source):
    from cifereca.core import missing_placement_dependency_projection as projection
    store, root, ids = rig
    pid = ids["unlinked.epub"]
    path = str(root / "a" / "unlinked.epub")
    item = store.placement_get(pid)
    if source == "metadata_key":
        store.metadata_set("synthetic:" + pid, {})
    elif source == "event_json_key":
        store.action_event_put(identity_id="b", action_type="test", outcome="observed",
                               facts={pid: "opaque"})
    elif source == "device_artifact":
        store.device_placement_put({"device_id": "device-test", "artifact_id": item["artifact_id"]})
    elif source == "device_path":
        store.device_placement_put({"device_id": "device-test", "artifact_id": "synthetic-other",
                                    "target_relative_path": path})
    elif source == "malformed_event":
        store.action_event_put(identity_id="b", action_type="test", outcome="observed")
        with store._lock, store._connect() as db:
            db.execute("UPDATE action_events SET facts_json='not-json'")
    else:
        monkeypatch.setattr(projection, "_MAX_REFERENCE_ROWS", 0)
    before = store.placements()
    report = _review(rig, "unlinked.epub")
    assert report["proposal"] == "preserve"
    assert not report["retirement_authorized"] and not report["archive_authorized"]
    assert store.placements() == before


def test_unreadable_checkpoint_preserves_even_unrelated_candidate(rig):
    store, root, ids = rig
    # metadata_set stores str(value), not a JSON serialization.
    # An opaque legacy Python repr cannot prove the absence of references.
    store.metadata_set("core.synthetic-opaque-checkpoint", {"undo": "unknown"})
    report = _review(rig, "unlinked.epub")
    assert report["proposal"] == "preserve"
    assert report["reason"] == "additional_evidence_unavailable"
    assert not report["archive_authorized"]
