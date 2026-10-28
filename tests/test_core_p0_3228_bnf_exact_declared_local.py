"""#3228: confirmed exact BnF loan/file declarations suppress redundant ACSM GET.

Real Core/SQLite and EPUB bytes, provider HTTPX fixture only; no live account,
ADEPT mutation, silent import, attachment, relocation or entitlement grant.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import library_declared_links as declarations
from cifereca.core import library_declared_presence as declared_presence
from cifereca.core.bnf_auto_materialization import materialize
from cifereca.core.library_inventory import scan_identity_roots
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_library_reconciliation import epub


def prepared(connected_core, tmp_path, monkeypatch):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    source_id = broker._source_id(account)
    assert broker.set_bnf_auto_download(source_id, True)
    service._persist_active_loans(account.identity_id, [loan], reconcile_missing=False)
    root = tmp_path / "EBooks" / "hugues" / "manual"
    book = root / "old-acsm" / "existing.epub"
    book.parent.mkdir(parents=True, exist_ok=True)
    epub(book, title=loan.title)
    observation, = scan_identity_roots({account.identity_id: (root,)}).files
    scope = dict(identity_id=account.identity_id, provider_id=loan.source_provider,
                 credential_id=account.credential_id, account_id=account.unique_id,
                 item_id=loan.record_id)
    monkeypatch.setattr(declarations, "acquisition_lock_file",
                        lambda: tmp_path / "declaration.lock")
    preview = declarations.declare_local_link(service, observation=observation, **scope)
    assert preview.status == "preview"
    confirmed = declarations.declare_local_link(
        service, observation=observation, **scope,
        confirmation=preview.plan_token,
    )
    assert confirmed.status == "declared"
    assert confirmed.rights_mutations == confirmed.file_mutations == 0
    return service, session, account, loan, downloaded, broker, source_id, book, scope


def test_user_declared_exact_bnf_loan_is_idempotently_local_without_a_second_get(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded, broker, sid, book, scope = prepared(
        connected_core, tmp_path, monkeypatch,
    )
    original_bytes = book.read_bytes()
    receipts_before = service.storage.acquisitions()
    placements_before = service.storage.placements()
    for _ in range(2):
        result = broker.synchronize_source(sid)
        assert result.facts["files_declared_local"] == 1
        assert result.facts["files_reconciliation_required"] == 0
        assert "files_materialized" not in result.facts
        assert "files_unchanged" not in result.facts
    assert downloaded == []
    assert service.storage.acquisitions() == receipts_before == []
    assert service.storage.placements() == placements_before == []
    assert book.read_bytes() == original_bytes


def test_declared_presence_requires_same_loan_id_even_when_edition_matches(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded, broker, sid, book, scope = prepared(
        connected_core, tmp_path, monkeypatch,
    )
    actual = declared_presence.verify_declared_presence(
        service, **scope, source_current=lambda: True,
        expected_loan_id=loan.id,
    )
    assert actual.status == "declared_current"
    wrong = declared_presence.verify_declared_presence(
        service, **scope, source_current=lambda: True,
        expected_loan_id="different-upstream-loan-id",
    )
    assert wrong.status == "needs_review" and wrong.reason == "loan_scope_changed"
    rotated = replace(loan, id="different-upstream-loan-id")
    assert materialize(service, account, rotated) == {
        "status": "attention_required",
        "code": "bnf_local_declaration_needs_review",
    }
    assert downloaded == [] and book.is_file()


@pytest.mark.parametrize("change", ["bytes", "right", "connection"])
def test_stale_declared_loan_blocks_get_without_mutating_old_file(
    connected_core, tmp_path, monkeypatch, change,
):
    service, session, account, loan, downloaded, broker, sid, book, scope = prepared(
        connected_core, tmp_path, monkeypatch,
    )
    if change == "bytes":
        book.write_bytes(b"changed synthetic EPUB")
    elif change == "right":
        current = service.storage.entitlements(
            provider_id=loan.source_provider, state="active",
        )[0]
        # A provider right may become inactive; its loan_id is an immutable
        # source coordinate and Storage correctly rejects a silent rewrite.
        service.storage.entitlement_put({**current, "state": "inactive"})
    else:
        service.storage.provider_connection_put({
            "identity_id": account.identity_id, "provider_id": account.provider_id,
            "credential_id": account.credential_id, "enabled": False,
        })
    outcome = materialize(service, account, loan)
    assert outcome["status"] == "attention_required"
    assert outcome["code"] in {
        "bnf_local_declaration_needs_review", "bnf_loan_scope_unproven",
    }
    assert downloaded == [] and book.is_file()
    assert service.storage.acquisitions(provider_id="manual") == []


def test_revoked_declaration_does_not_veto_independent_opted_in_delivery(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, downloaded, broker, sid, book, scope = prepared(
        connected_core, tmp_path, monkeypatch,
    )
    attempted = []
    def synthetic_stop(*_a, **_kw):
        attempted.append(True)
        raise RuntimeError("synthetic stop before signed GET")
    monkeypatch.setattr(session, "download_digital", synthetic_stop)
    original = book.read_bytes()
    from tests.test_library_declared_links import records
    record, = [r for r in records(service) if r["scope"] == scope]
    # Revoking removes ONLY the user's statement. Physical preflight may
    # still independently flag the same OPF title as a candidate for review.
    observation, = scan_identity_roots({
        account.identity_id: (book.parents[1],)
    }).files
    preview = declarations.declare_local_link(
        service, observation=observation, **scope, revoke=record["relation_id"],
    )
    assert preview.status == "revoke_preview"
    undone = declarations.declare_local_link(
        service, observation=observation, **scope, revoke=record["relation_id"],
        confirmation=preview.plan_token,
    )
    assert undone.status == "revoked"
    outcome = materialize(service, account, loan)
    assert outcome == {
        "status": "attention_required",
        "code": "bnf_auto_acquisition_failed",
    }
    assert attempted == [True] and not downloaded
    assert book.read_bytes() == original
