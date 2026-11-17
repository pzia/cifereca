"""#6366: the real History worker binds its private borrower only after lease.

A provider-normalized HistoryItem can omit identity/account coordinates.  The
canonical public C is independently attested by Search and supplies only a
thumbnail; a private account cache is neither a public edition nor a right.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.library_projection import (
    _history_cache_key, _pinned_history_c_rows, refresh_history,
)
from cifereca.core.notice_c_borrower_public_projection import (
    borrower_public_c_receipt,
)
from cifereca.gui.borrower_public_c_bridge import (
    exact_history_public_c, show_public_history_c,
)
from cifereca.models import Account, HistoryItem
from cifereca.storage import Storage
from tests.test_core_p0_6366_borrower_public_c_lists_detail import bibliography, borrower_descriptors


def _account():
    return Account(
        unique_id="reader-a", provider_id="paris",
        credential_id="card-a", identity_id="alice", is_primary=False,
    )


def _provider_history(**change):
    # Same DTO shape as Paris normalize_history + _decorate: the supplier
    # provides the DILICOM book record but does not include borrower fields.
    row = HistoryItem(
        id="DILICOM:edition-paris:2026", provider="DILICOM",
        medium="digital", record_id="edition-paris",
        title="Livre source", author="Autrice", thumbnail_url=None,
        source_provider="paris", source_credential="card-a",
        identity_id=None, source_account_id=None,
    )
    for key, value in change.items():
        setattr(row, key, value)
    return row


def _service(db, account, row):
    state = {"leased": True, "reads": 0}
    def read(original):
        assert original is account
        state["reads"] += 1
        return [row]
    session = NS(
        capabilities=NS(history=True), history=read,
    )
    service = NS(
        storage=db, current_account=account,
        _account_context_epoch=7, _source_context_epoch=9,
        config=NS(get=lambda _key, default: default),
        diagnostics=NS(add=lambda *_a, **_kw: None),
        last_history=[], provider_descriptors=borrower_descriptors,
        # Cached history uses the already-registered Core session without
        # reconnecting: model the same native session registry and key here.
        _sessions={(account.provider_id, account.credential_id): session},
        _session_key=lambda provider, credential: (provider, credential),
        _session_for_account=lambda target: (
            session if target is account else None
        ),
        _history_source_lease_current=lambda target, current, *,
            scope, epoch: (
                state["leased"] and target is account
                and current is session
                and scope == (
                    account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id,
                ) and epoch == 9
            ),
    )
    return service, state


def test_live_and_cached_paris_history_join_exact_c_without_private_cache_proof(
    tmp_path,
):
    db = Storage(tmp_path / "history-public-c.sqlite")
    try:
        observed, c = bibliography(
            db, provider="paris", record="edition-paris",
            docbase="DILICOM",
        )
        account = _account()
        row = _provider_history()
        service, state = _service(db, account, row)
        live = refresh_history(service, force=True, account=account)
        assert live == [row] and state["reads"] == 1
        assert row.identity_id == "alice"
        assert row.source_account_id == "reader-a"
        receipt = borrower_public_c_receipt(
            row, identity_id="alice", account_epoch=7, source_epoch=9,
        )
        assert receipt is not None
        assert receipt.document == c.document
        assert receipt.source_ref == observed.document.source_key

        # Stamping happens after cache write. Private cache has neither an
        # ownership-derived public witness nor synthesized Core owner fields.
        raw = db.cache_get("account", _history_cache_key(account))
        assert len(raw) == 1
        assert raw[0]["identity_id"] is None
        assert raw[0]["source_account_id"] is None
        assert "_core_public_c_cover_receipt" not in raw[0]
        service.last_history = []
        state["reads"] = 0
        cached = refresh_history(service, force=False, account=account)
        assert state["reads"] == 0
        assert cached[0] is not row
        assert cached[0].identity_id == "alice"
        assert cached[0].source_account_id == "reader-a"
        restored = borrower_public_c_receipt(
            cached[0], identity_id="alice",
            account_epoch=7, source_epoch=9,
        )
        assert restored is not None and restored.document == c.document

        # The actual GUI bridge consumes this original pinned item, not the
        # detached value created by dataclass serialization.
        host = NS(
            service=NS(
                current_identity=NS(identity_id="alice"),
                _account_context_epoch=7, _source_context_epoch=9,
            ),
            _history_cover_revision=1,
            history_objects={cached[0].id: cached[0]},
            history_page=NS(detail=NS()),
        )
        assert exact_history_public_c(host, cached[0]) is restored
        shown = []
        host.history_page.detail.show_history_public_c = (
            lambda item, *, document: shown.append((item, document))
        )
        assert show_public_history_c(host, cached[0], cached[0])
        assert shown == [(cached[0], c.document)]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_cached_history_is_visible_but_c_link_fails_closed_after_lease_revocation(
    tmp_path,
):
    db = Storage(tmp_path / "history-revoked.sqlite")
    try:
        bibliography(
            db, provider="paris", record="edition-paris", docbase="DILICOM",
        )
        account = _account()
        service, state = _service(db, account, _provider_history())
        assert refresh_history(service, force=True, account=account)
        state["leased"] = False
        state["reads"] = 0
        rows = refresh_history(service, force=False, account=account)
        assert len(rows) == 1 and state["reads"] == 0
        assert borrower_public_c_receipt(
            rows[0], identity_id="alice",
        ) is None
        assert rows[0].identity_id is None
        assert rows[0].source_account_id is None
        assert db.entitlements() == []
    finally:
        db.close()


@pytest.mark.parametrize("corrupt", [
    {"source_provider": "numilog"},
    {"source_credential": "other-card"},
    {"identity_id": "other-identity"},
    {"source_account_id": "another-borrower"},
    {"source_account_id": False},
    {"identity_id": False},
])
def test_pinned_history_never_overwrites_conflicting_upstream_borrower(
    corrupt,
):
    account = _account()
    row = _provider_history(**corrupt)
    original = (row.identity_id, row.source_account_id)
    assert _pinned_history_c_rows([row], account, descriptors=borrower_descriptors()) == []
    assert (row.identity_id, row.source_account_id) == original


def test_history_cache_row_pinned_to_wrong_account_never_borrows_c(tmp_path):
    db = Storage(tmp_path / "wrong-account-history.sqlite")
    try:
        bibliography(
            db, provider="paris", record="edition-paris", docbase="DILICOM",
        )
        account = _account()
        service, _ = _service(
            db, account,
            _provider_history(source_account_id="foreign-account"),
        )
        rows = refresh_history(service, force=True, account=account)
        assert len(rows) == 1
        assert borrower_public_c_receipt(
            rows[0], identity_id="alice",
        ) is None
        cached = refresh_history(service, force=False, account=account)
        assert cached[0].source_account_id == "foreign-account"
        assert borrower_public_c_receipt(
            cached[0], identity_id="alice",
        ) is None
        assert db.entitlements() == []
    finally:
        db.close()


def test_supplier_branch_change_revokes_previously_admitted_history_c(tmp_path):
    db = Storage(tmp_path / "branch-changed.sqlite")
    try:
        bibliography(
            db, provider="paris", record="edition-paris", docbase="DILICOM",
        )
        account = _account()
        source = _provider_history()
        service, _state = _service(db, account, source)
        original = refresh_history(service, force=True, account=account)[0]
        accepted = borrower_public_c_receipt(
            original, identity_id="alice", account_epoch=7, source_epoch=9,
        )
        assert accepted is not None
        # An occurrence can retain provider+record+medium while its upstream
        # branch is changed by a later refresh or a faulty plugin.
        original.provider = "SYRACUSE"
        assert borrower_public_c_receipt(
            original, identity_id="alice",
        ) is None
        original.provider = "DILICOM"
        # Return to the original scalar fields is not independently a new
        # Core lease; a newly admitted worker batch reconstructs a receipt.
        refresh_history(service, force=True, account=account)
        assert db.entitlements() == []
    finally:
        db.close()


@pytest.mark.parametrize("change", [
    {"provider": "SYRACUSE", "medium": "physical"},
    {"provider": "UNSUPPORTED", "medium": "digital"},
    {"thumbnail_url": "https://covers.example.org/direct-provider.jpg"},
])
def test_missing_native_edition_proof_does_not_rewrite_history_owner(change):
    account = _account()
    item = _provider_history(**change)
    assert _pinned_history_c_rows([item], account, descriptors=borrower_descriptors()) == []
    assert item.identity_id is None
    assert item.source_account_id is None


def test_history_without_installed_branch_declaration_preserves_raw_owner(tmp_path):
    db = Storage(tmp_path / "no-branch-declaration.sqlite")
    try:
        bibliography(db, provider="paris", record="edition-paris", docbase="DILICOM")
        account = _account()
        row = _provider_history()
        service, state = _service(db, account, row)
        service.provider_descriptors = lambda: ()
        assert refresh_history(service, force=True, account=account) == [row]
        assert state["reads"] == 1
        assert row.identity_id is None and row.source_account_id is None
        assert borrower_public_c_receipt(row, identity_id="alice") is None
        cached, = refresh_history(service, force=False, account=account)
        assert state["reads"] == 1
        assert cached.identity_id is None and cached.source_account_id is None
        assert borrower_public_c_receipt(cached, identity_id="alice") is None
    finally:
        db.close()
