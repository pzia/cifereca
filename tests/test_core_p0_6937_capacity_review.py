"""#6937: capacity observation over real private receipts and isolated SQLite/FS."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import derived_variant_capacity_review as core
from cifereca.core import derived_variant_journal as journal
from cifereca.core.derived_variant_maintenance_review import admit_maintenance_review
from cifereca.gui import derived_variant_capacity_line as gui
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container


def _read(service):
    return core.read_capacity_review(service, admit_maintenance_review(service))


def test_capacity_and_physical_stock_separate(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", intent.output_size)
        before = db.metadata_get(reservation.key)
        result = _read(service)
        assert result["status"] == "observed"
        assert result["counts"] == {
            "pending": 1, "committed": 0, "unreadable": 0, "unbound": 0,
            "active_pending": 1, "active_committed": 0, "other_bound": 0,
        }
        assert result["record_count"] == 1
        assert result["known_output_bytes"] == result["active_output_bytes"] == intent.output_size
        assert result["payload_bytes"] == result["active_payload_bytes"] == len(before.encode("utf-8"))
        assert result["saturation"] == "at_limit"
        stock = result["stock"]
        assert stock["observations"]["same_inode_target"] == 1
        assert stock["observed_unique_inode_logical_bytes"] == intent.output_size
        assert stock["complete_physical_inventory"] is False and stock["sha_verified"] is False
        assert result["quota_refund_authorized"] is False
        assert "Plafond atteint" in gui._subtitle(result)
        assert str(tmp_path) not in repr(result)
        assert reservation.key not in repr(result)
        assert db.metadata_get(reservation.key) == before
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_unbound_and_corrupt_receipts_are_not_free_capacity(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        service.config.identities = lambda: [
            {"id": "id1", "directory_name": "library"},
        ]
        # Synthetic detached receipt, exact format but no configured owner.
        foreign = replace(intent, identity_id="retired", parent_placement_id="other")
        from dataclasses import asdict
        raw = journal._encode({"schema": "derived.intent.v2", "nonce": "a"*32,
                               "intent": asdict(foreign)})
        key = journal._slot(foreign)
        db.metadata_set(key, raw)
        result = _read(service)
        assert result["status"] == "partial" and result["counts"]["unbound"] == 1
        assert result["known_output_bytes"] == 2 * intent.output_size
        assert result["active_output_bytes"] == intent.output_size
        db.metadata_set(key, "not valid")
        result = _read(service)
        assert result["status"] == "partial" and result["counts"]["unreadable"] == 1
        assert result["saturation"] == "unknown" and not result["output_bytes_complete"]
        assert result["known_output_bytes"] == intent.output_size
        assert result["stock"]["status"] == "unavailable"
        assert "indisponible" in gui._subtitle(result)
        assert db.metadata_get(key) == "not valid"
        assert db.metadata_get(reservation.key) is not None
    finally:
        db.close()


def test_native_blob_receipt_reports_partial_without_discarding_capacity(tmp_path):
    """SQLite BLOB is unreadable provenance, not evidence of free capacity."""
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        original = db.metadata_get(reservation.key)
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE metadata SET value=? WHERE key=?",
                        (original.encode("utf-8"), reservation.key))
        result = _read(service)
        assert result is not None
        assert result["status"] == "partial"
        assert result["counts"]["pending"] == 1
        assert result["counts"]["unreadable"] == 1
        assert result["record_count"] == 1
        assert result["output_bytes_complete"] is False
        assert result["saturation"] == "unknown"
        assert result["known_output_bytes"] == 0
        assert result["admission_authorized"] is False
        assert result["quota_refund_authorized"] is False
        assert result["destructive_cleanup_allowed"] is False
        with db._lock:
            stored = db._connect().execute(
                "SELECT typeof(value),value FROM metadata WHERE key=?",
                (reservation.key,),
            ).fetchone()
        assert stored == ("blob", original.encode("utf-8"))
        assert Path(intent.staged_path).exists()
    finally:
        db.close()


def test_scope_or_snapshot_change_discards_result(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    original = core.review_derived_staging_stock

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        db.metadata_set("synthetic.concurrent-change", "1")
        return result

    monkeypatch.setattr(core, "review_derived_staging_stock", changed)
    try:
        assert _read(service) is None
    finally:
        db.close()


@pytest.mark.parametrize("stale", [False, True])
def test_native_capacity_line_click_and_duplicate_delivery(tmp_path, stale):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        jobs = []
        host = NS(service=service, _closed=False, _route_display_token=object(),
                  action_button=lambda label, _icon: Button(label),
                  runner=NS(submit=lambda *args, **kw: jobs.append(args)))
        box = Container()
        gui.add_derived_variant_capacity_line(host, box, Adw=NS(PreferencesGroup=Container,
                                                                ActionRow=Container))
        row = box.children[0].children[0]
        button, = row.children
        assert not jobs
        button.click()
        button.click()
        assert len(jobs) == 1
        value = jobs[0][1]()
        if stale:
            host._route_display_token = object()
        jobs[0][2](value)
        initial = row.subtitle
        jobs[0][2](None)
        assert row.subtitle == initial
        assert ("Capacité indisponible" if stale else "aucun octet récupérable attesté") in initial
        assert db.metadata_get(reservation.key) is not None
    finally:
        db.close()


@pytest.mark.parametrize("directory", [
    "library\n", "library\x7f", "library\x00",
    "x" * 257, ".", "..", "library/sub",
    "library\x85hidden", "library\u202ehidden",
    "library\u2028hidden", "library\u200dhidden",
])
def test_capacity_never_recognizes_untrusted_profile_directory_names(
    tmp_path, directory,
):
    # A corrupt identity binding must not become an active capacity witness.
    # Neither receipt, staged inode nor authoritative original is modified.
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        service.config.identities = lambda: [
            {"id": "id1", "directory_name": directory},
        ]
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE identities SET directory_name=? WHERE identity_id='id1'",
                (directory,),
            )
        scope = NS(storage=db, config=service.config, root=tmp_path,
                   identity_id="id1", directory="library")
        snapshot = core._snapshot(scope)
        assert not core._valid_profile_directory(directory)
        assert snapshot[1] == (("id1", None),)
        assert snapshot[2]["unbound"] == 1
        assert snapshot[2]["active_pending"] == 0
        assert snapshot[2]["active_committed"] == 0
        assert db.metadata_get(reservation.key) is not None
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_capacity_directory_guard_preserves_existing_valid_names():
    assert core._valid_profile_directory("library")
    assert core._valid_profile_directory("Bibliothèque privée")
    assert core._valid_profile_directory("z" * 256)


@pytest.mark.parametrize("second", ["library", "different-library"])
def test_duplicate_configured_owner_never_counts_as_bound_retention_capacity(
    tmp_path, second,
):
    service, intent, reservation = _setup(tmp_path)
    storage = service.storage
    try:
        service.config.identities = lambda: [
            {"id": "id1", "directory_name": "library"},
            {"id": "id1", "directory_name": second},
        ]
        receipt = storage.metadata_get(reservation.key)
        stage = Path(intent.staged_path)
        before_stage = stage.read_bytes()
        scope = NS(storage=storage, config=service.config, root=tmp_path,
                   identity_id="id1", directory="library")
        snapshot = core._snapshot(scope)
        assert snapshot[1] == (("id1", None),)
        counts = snapshot[2]
        assert counts["unbound"] == 1
        assert counts["active_pending"] == 0
        assert counts["active_committed"] == 0
        assert counts["other_bound"] == 0
        assert storage.metadata_get(reservation.key) == receipt
        assert stage.read_bytes() == before_stage
        assert stage.samefile(intent.candidate_path)
    finally:
        storage.close()


@pytest.mark.parametrize("case", [
    "empty", "too_many", "tuple_rows", "generator", "non_dict",
    "no_id", "invalid_id", "invalid_id_del", "invalid_id_too_long",
    "invalid_id_not_text", "no_directory",
    "invalid_id_c1", "invalid_id_bidi", "invalid_id_separator", "invalid_id_zwj",
])
def test_capacity_refuses_unbounded_or_malformed_config_owner_inventory(
    tmp_path, case,
):
    service, intent, reservation = _setup(tmp_path)
    storage = service.storage
    stage = Path(intent.staged_path)
    prior = stage.read_bytes(), storage.metadata_get(reservation.key)
    baseline = {"id": "id1", "directory_name": "library"}
    variants = {
        "empty": [],
        "too_many": [baseline] * 129,
        "tuple_rows": (baseline,),
        "generator": (x for x in [baseline]),
        "non_dict": [None],
        "no_id": [{"directory_name": "library"}],
        "invalid_id": [{"id": "id1\nforged", "directory_name": "library"}],
        "invalid_id_del": [{"id": "id1\x7fforged", "directory_name": "library"}],
        "invalid_id_too_long": [{"id": "x" * 1025, "directory_name": "library"}],
        "invalid_id_not_text": [{"id": None, "directory_name": "library"}],
        "invalid_id_c1": [{"id": "id1\x85fake", "directory_name": "library"}],
        "invalid_id_bidi": [{"id": "id1\u202efake", "directory_name": "library"}],
        "invalid_id_separator": [{"id": "id1\u2028fake", "directory_name": "library"}],
        "invalid_id_zwj": [{"id": "id1\u200dfake", "directory_name": "library"}],
        "no_directory": [{"id": "id1"}],
    }
    try:
        service.config.identities = lambda: variants[case]
        scope = NS(storage=storage, config=service.config, root=tmp_path,
                   identity_id="id1", directory="library")
        with pytest.raises(core.journal.DerivedJournalConflict,
                           match="derived_capacity_config_unverified"):
            core._snapshot(scope)
        assert storage.metadata_get(reservation.key) == prior[1]
        assert stage.read_bytes() == prior[0]
        assert stage.samefile(intent.candidate_path)
    finally:
        storage.close()


@pytest.mark.parametrize("identity_rows", [
    [],
    [{"id": "id1", "directory_name": "library"}] * 129,
    ({"id": "id1", "directory_name": "library"},),
    [{"id": "id1", "directory_name": "library"}, None],
    [{"id": 123, "directory_name": "library"}],
    [{"id": "", "directory_name": "library"}],
])
def test_capacity_refuses_unbounded_or_invalid_config_identity_inventory(
    tmp_path, identity_rows,
):
    service, intent, reservation = _setup(tmp_path)
    storage = service.storage
    try:
        stage = Path(intent.staged_path)
        original = stage.read_bytes()
        prior = storage.metadata_get(reservation.key)
        service.config.identities = lambda: identity_rows
        scope = NS(storage=storage, config=service.config, root=tmp_path,
                   identity_id="id1", directory="library")
        with pytest.raises(journal.DerivedJournalConflict,
                           match="derived_capacity_config_unverified"):
            core._snapshot(scope)
        assert storage.metadata_get(reservation.key) == prior
        assert stage.read_bytes() == original
        assert stage.samefile(intent.candidate_path)
    finally:
        storage.close()


@pytest.mark.parametrize("raw", [
    "unpaired-\ud800", "unpaired-\udfff", "pair-\ud800broken",
])
def test_unencodable_unicode_receipt_is_closed_conflict_not_raw_codec_error(raw):
    key = journal._PREFIX + "0" * 64
    with pytest.raises(journal.DerivedJournalConflict, match="derived_journal_corrupt"):
        journal._decode(key, raw)
    with pytest.raises(journal.DerivedJournalConflict, match="derived_capacity_receipt_unverified"):
        core._receipt_witness(raw)
