"""#976 — synthetic local history never grants an ADEPT return from v14 links."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import cli

from cifereca.core.adept_transaction_reconciliation import (
    review_paris_adept_transaction,
)
from cifereca.storage import Storage
from tests.test_core_0976_historical_origin_audit import _acq, _right
from tests.test_core_p1_976_canonical_originlink_review import (
    _artifact, _link, _source,
)


class EvidenceStore:
    """Real SQLite v14, independent legacy history; no live provider calls."""
    def __init__(self, storage, *, rights=(), acquisitions=()):
        self._lock = storage._lock
        self._connect = storage._connect
        self.rights = list(rights)
        self.ledger = list(acquisitions)
        self.calls = []

    def entitlements(self, *, provider_id):
        assert provider_id == "paris"
        self.calls.append("rights")
        return self.rights

    def acquisitions(self, *, provider_id):
        assert provider_id == "paris"
        self.calls.append("ledger")
        return self.ledger


def _review(store, loan="paris-loan", item="item-1"):
    return review_paris_adept_transaction(
        store, loan_id=loan, item_id=item,
        artifact_inspector=lambda _: None,
    )


def _canonical(db, ref="source-a", *, identity="alice", credential="paris-a",
               branch="loans", state="active", paired=True):
    _source(db, ref, identity=identity, credential=credential,
            branch=branch)
    db.execute(
        "UPDATE source_references SET state=? WHERE source_reference_id=?",
        (state, ref),
    )
    _link(db, ref + "-provider", "provider", ref, identity,
          proof="SECRET-PROVIDER-" + ref)
    if paired:
        _link(db, ref + "-adept", "adept", ref, identity,
              proof="SECRET-ADOBE-" + ref)


def _historical_right(identity="alice", credential="paris-a"):
    return _right(identity, credential, loan="paris-loan", item="item-1")


def _historical_acq(identity="alice", credential="paris-a", profile="adobe-a",
                    *, attested=True):
    return _acq(
        identity, credential, profile, loan="paris-loan", item="item-1",
        state="attested" if attested else "recorded",
    )


def test_attested_old_inventory_and_paired_new_links_only_overlap_account(tmp_path):
    db = Storage(tmp_path / "paired.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn)
        store = EvidenceStore(
            db, rights=[_historical_right()],
            acquisitions=[_historical_acq()],
        )
        first = _review(store)
        second = _review(store)
        assert first == second
        assert first["status"] == "scope_overlap_only"
        assert first["canonical_link_resolution"] == "paired_links_unverified"
        assert first["historical_origin_resolution"] == "single_inventory_attested"
        assert first["historical_inventory_status"] == "inventory_attested"
        assert first["review_reasons"] == []
        assert first["sources"][0]["legacy_scope_overlap"] == (
            "persisted_entitlement_scope_only"
        )
        assert first["sources"][0]["historical_acquisition_count"] == 1
        assert first["sources"][0]["canonical_link_pairs_unverified"] == 1
        assert first["adobe_profile_resolution"] == "unknown"
        assert first["requires_explicit_review"]
        assert not first["return_authorized"]
        assert "SECRET-" not in repr(first)
        assert store.calls == ["rights", "ledger", "rights", "ledger"]
    finally:
        db.close()


def test_same_sha_two_independent_paris_borrowers_keep_separate_history(tmp_path):
    db = Storage(tmp_path / "two-borrowers.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn, "source-a", identity="alice", credential="paris-a")
            _canonical(conn, "source-b", identity="bob", credential="paris-b")
        store = EvidenceStore(db, rights=[
            _historical_right("alice", "paris-a"),
            _historical_right("bob", "paris-b"),
        ], acquisitions=[_historical_acq("alice", "paris-a")])
        result = _review(store)
        assert result["status"] == "scope_overlap_only"
        assert len(result["sources"]) == 2
        assert {s["source_reference_id"] for s in result["sources"]} == {
            "source-a", "source-b",
        }
        counts = {
            s["credential_id"]: s["historical_acquisition_count"]
            for s in result["sources"]
        }
        assert counts == {"paris-a": 1, "paris-b": 0}
        assert all(s["historical_entitlement_present"] for s in result["sources"])
        assert not result["return_authorized"]
    finally:
        db.close()


def test_acquisition_only_scope_cannot_be_promoted_to_a_paris_right(tmp_path):
    db = Storage(tmp_path / "acquisition-only.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn)
        result = _review(EvidenceStore(
            db, acquisitions=[_historical_acq()],
        ))
        assert result["status"] == "evidence_gap"
        assert "historical_entitlement_missing" in result["review_reasons"]
        assert "historical_acquisition_without_entitlement" in result["review_reasons"]
        assert result["sources"][0]["legacy_scope_overlap"] == (
            "acquisition_only_scope"
        )
        assert result["sources"][0]["historical_acquisition_count"] == 1
        assert result["sources"][0]["historical_entitlement_present"] is False
    finally:
        db.close()


def test_same_sha_different_account_never_matches_legacy_acquisition(tmp_path):
    db = Storage(tmp_path / "wrong-account.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn, "source-b", identity="bob", credential="paris-b")
        result = _review(EvidenceStore(
            db, rights=[_historical_right("alice", "paris-a")],
            acquisitions=[_historical_acq("alice", "paris-a")],
        ))
        assert result["status"] == "evidence_gap"
        assert result["sources"][0]["historical_acquisition_count"] == 0
        assert result["sources"][0]["legacy_scope_overlap"] == "no_historical_scope"
        assert result["historical_unmatched_source_count"] == 1
        assert result["historical_unmatched_acquisition_count"] == 1
        assert "historical_acquisition_without_canonical_source" in result["review_reasons"]
        assert not result["return_authorized"]
    finally:
        db.close()


def test_two_canonical_branches_same_paris_account_cannot_be_joined_to_old_right(
    tmp_path,
):
    db = Storage(tmp_path / "ambiguous-branches.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn, "source-branch-a", branch="paris-borrowed")
            _canonical(conn, "source-branch-b", branch="paris-linked")
        result = _review(EvidenceStore(
            db, rights=[_historical_right()],
            acquisitions=[_historical_acq()],
        ))
        assert result["status"] == "conflicting_records"
        assert result["review_reasons"] == ["historical_branch_unresolvable"]
        assert all(s["legacy_scope_overlap"] == "ambiguous_canonical_branch"
                   for s in result["sources"])
        assert not result["return_authorized"]
    finally:
        db.close()


def test_historical_adobe_profile_conflict_never_resolved_by_new_links(tmp_path):
    db = Storage(tmp_path / "historical-conflict.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn)
        result = _review(EvidenceStore(
            db, rights=[_historical_right()],
            acquisitions=[
                _historical_acq(profile="adobe-original"),
                _historical_acq(profile="adobe-other"),
            ],
        ))
        assert result["historical_origin_resolution"] == "conflicting_records"
        assert result["status"] == "conflicting_records"
        assert "historical_adobe_claims_conflict" in result["review_reasons"]
        assert result["adobe_profile_resolution"] == "unknown"
    finally:
        db.close()


def test_non_active_canonical_source_is_not_an_implicit_revocation(tmp_path):
    db = Storage(tmp_path / "source-inactive.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn, state="inactive")
        result = _review(EvidenceStore(
            db, rights=[_historical_right()],
            acquisitions=[_historical_acq()],
        ))
        assert result["status"] == "evidence_gap"
        assert result["review_reasons"] == ["canonical_source_not_attested_active"]
        assert result["sources"][0]["canonical_source_state"] == "inactive"
        assert not result["return_authorized"]
    finally:
        db.close()


def test_no_canonical_source_never_migrates_legacy_evidence_implicitly(tmp_path):
    db = Storage(tmp_path / "legacy-only.db")
    try:
        result = _review(EvidenceStore(
            db, rights=[_historical_right()],
            acquisitions=[_historical_acq()],
        ))
        assert result["status"] == "evidence_gap"
        assert "canonical_exact_source_missing" in result["review_reasons"]
        assert result["historical_unmatched_source_count"] == 1
        assert result["historical_unmatched_acquisition_count"] == 1
        assert result["sources"] == []
    finally:
        db.close()


def test_invalid_transaction_keys_never_access_storage_or_inspector():
    class DoNotOpen:
        def __getattribute__(self, name):
            raise AssertionError("invalid request must not access storage")
    for loan, item in [
        ("", "item-1"), ("loan-1", "  "), ("\x00secret", "item-1"),
        ({"token": "PRIVATE"}, "item-1"),
    ]:
        result = review_paris_adept_transaction(
            DoNotOpen(), loan_id=loan, item_id=item,
            artifact_inspector=lambda _: (_ for _ in ()).throw(
                AssertionError("must not inspect artifact")
            ),
        )
        assert result["status"] == "unknown"
        assert result["review_reasons"] == ["invalid_exact_scope"]
        assert result["sources"] == []
        assert result["return_authorized"] is False
        assert "PRIVATE" not in repr(result)


def test_cli_transaction_bridge_reads_both_layers_without_provider_connection(
    tmp_path, monkeypatch, capsys,
):
    db = Storage(tmp_path / "cli-bridge.db")
    try:
        _artifact(db)
        with db._lock, db._connect() as conn:
            _canonical(conn)
        storage = EvidenceStore(
            db, rights=[_historical_right()],
            acquisitions=[_historical_acq()],
        )

        def forbidden_connection(*args, **kwargs):
            raise AssertionError("transaction audit must not connect to Paris")

        core = SimpleNamespace(
            storage=storage,
            library=SimpleNamespace(connect_configured=forbidden_connection),
            close=lambda: None,
        )
        monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_: core)
        result = cli.main([
            "paris-adept", "audit-transaction",
            "--loan-id", "paris-loan", "--item-id", "item-1",
        ])
        report = json.loads(capsys.readouterr().out)
        assert result == 0
        assert report["status"] == "scope_overlap_only"
        assert report["sources"][0]["legacy_scope_overlap"] == (
            "persisted_entitlement_scope_only"
        )
        assert not report["return_authorized"]
        assert report["adobe_profile_resolution"] == "unknown"
        assert storage.calls == ["rights", "ledger"]
    finally:
        db.close()


@pytest.mark.parametrize(("loan", "item"), [
    (" ", "item-1"), ("paris-loan", ""), ("paris-loan", "\x00item"),
])
def test_cli_bridge_bad_transaction_keys_are_refused_before_core_open(
    monkeypatch, loan, item,
):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("invalid transaction keys must not open Core")
        ),
    )
    with pytest.raises(SystemExit) as exc:
        cli.main([
            "paris-adept", "audit-transaction",
            "--loan-id", loan, "--item-id", item,
        ])
    assert exc.value.code == 2
