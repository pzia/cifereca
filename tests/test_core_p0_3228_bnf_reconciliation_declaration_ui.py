"""#3228: existing private reconciliation can confirm an exact BnF/manual pair.

Real Core/SQLite, file inventory and existing CoreUI declaration bridge.
All source/provider data are synthetic; no real BnF, ACSM or ADEPT I/O.
"""
from __future__ import annotations

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from cifereca.core.library_audit import audit_library
from cifereca.core.library_declared_links import recorded_declared_inspection
from cifereca.core.library_local_inspection import LocalInspection, PROVIDERS
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_library_reconciliation import epub


def _inspection(connected_core, tmp_path, *, right_kind="loan", loan_id=True, active=True):
    service, session, account, loan, state, downloaded = connected_core
    root = tmp_path / "EBooks" / "hugues"
    book = root / "manual" / "old-import" / "already-here.epub"
    book.parent.mkdir(parents=True, exist_ok=True)
    epub(book, title=loan.title)

    if right_kind == "loan" and loan_id and active:
        service._persist_active_loans(account.identity_id, [loan],
                                      reconcile_missing=False)
    else:
        service.storage.entitlement_put({
            "entitlement_id": "synthetic-bnf-entitlement",
            "identity_id": account.identity_id,
            "provider_id": "bnf-pret",
            "credential_id": account.credential_id,
            "account_id": account.unique_id,
            "item_id": loan.record_id,
            "entitlement_type": right_kind,
            "state": "active" if active else "inactive",
            "loan_id": loan.id if loan_id else None,
            "metadata": {"title": loan.title},
        })
    audit = audit_library(service.storage.path, {"default": (root,)})
    assert audit.redacted()["status"] == "complete", audit.redacted()["reasons"]
    inspector = LocalInspection(audit, lambda: True, "default",
                                roots=(root,), service=service)
    return service, session, account, loan, downloaded, book, inspector


def test_bnf_right_has_own_filter_and_existing_ui_confirmation_then_zero_get(
    connected_core, tmp_path,
):
    service, session, account, loan, downloaded, book, inspector = _inspection(
        connected_core, tmp_path,
    )
    broker = SynchronizationBrokerImpl(service)
    original = book.read_bytes()
    assert PROVIDERS["bnf-pret"] == "BnF Prêt"
    try:
        right, = inspector.page(provider="bnf-pret",
                                state="rights_without_verified_file").cases
        file, = inspector.unconfirmed_files(limit=10).cases
        assert right.provider == "bnf-pret"
        context = inspector.declaration_context(right.key, file.key)
        assert context is not None
        assert (context["identity"], context["provider"], context["account"],
                context["edition"]) == (
                    account.identity_id, "bnf-pret", account.unique_id,
                    loan.record_id,
                )
        assert inspector.confirm_declared_link(
            right.key, file.key, user_confirmed=True,
        )["status"] == "unavailable"
        before = service.storage.acquisitions()
        plan = inspector.preview_declared_link(right.key, file.key)
        assert plan["status"] == "preview"
        assert plan["requires_explicit_confirmation"] is True
        assert plan["trust"] == "user_declaration"
        assert service.storage.acquisitions() == before == []
        assert inspector.confirm_declared_link(
            right.key, file.key, user_confirmed=False,
        )["status"] == "unavailable"
        assert inspector.preview_declared_link(right.key, file.key)["status"] == "preview"
        result = inspector.confirm_declared_link(
            right.key, file.key, user_confirmed=True,
        )
        assert result["status"] == "declared"
        assert result["rights_mutations"] == result["file_mutations"] == 0
        assert result["placement_mutations"] == 0
    finally:
        inspector.close()

    assert not service.storage.acquisitions()
    assert not service.storage.placements()
    assert book.read_bytes() == original
    assert not downloaded
    scopes, pairs = recorded_declared_inspection(
        service.storage, identity_id=account.identity_id,
    )
    scope = (account.identity_id, "bnf-pret", account.credential_id,
             account.unique_id, loan.record_id)
    assert scope in scopes
    assert pairs

    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    assert materialize(service, account, loan) == {"status": "declared_local"}
    for _ in range(2):
        response = broker.synchronize_source(broker._source_id(account))
        assert response.facts["files_declared_local"] == 1
        # The broker still counts the regular loan inventory refresh as a
        # change; a declaration must not add an acquisition to that total.
        assert response.changed == response.facts["loans"]
    assert downloaded == []
    assert book.read_bytes() == original
    assert not service.storage.acquisitions()
    assert not service.storage.placements()


@pytest.mark.parametrize("kind,loan_id,active", [
    ("owned", False, True),
    ("loan", False, True),
    ("loan", True, False),
])
def test_bnf_catalogue_or_non_current_right_cannot_be_user_declared_as_loan(
    connected_core, tmp_path, kind, loan_id, active,
):
    service, _session, account, loan, downloaded, book, inspector = _inspection(
        connected_core, tmp_path,
        right_kind=kind, loan_id=loan_id, active=active,
    )
    try:
        rights = inspector.page(provider="bnf-pret").cases
        files = inspector.unconfirmed_files(limit=10).cases
        assert len(files) == 1
        assert all(
            inspector.declaration_context(right.key, files[0].key) is None
            for right in rights if right.key.startswith("right:")
        )
        assert service.storage.acquisitions() == []
        assert not downloaded and book.is_file()
    finally:
        inspector.close()
