"""P0 #6366: Paris DILICOM exact personal RecordId -> public C N2.

Original provider-preserved private account rows never become public
bibliographies/rights. The only C link comes from an independent, already
persisted public SearchService C for provider paris / RscBase DILICOM /
RscId same as the *account API* RecordId.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_source_identity import loan_source_key
from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
)
from cifereca.core.notice_c_paris_personal import (
    VerifiedParisPersonalPublicC, attach_paris_personal_public_c,
    valid_paris_personal_public_c,
)
from cifereca.gui.notice_c_paris_personal_bridge import (
    current_paris_personal_public_c, public_c_is_current,
    show_paris_public_loan_cover,
)
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.models import Account, HistoryItem, Loan
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


PUBLIC = "https://covers.example.org/paris-exact-dilicom.jpg"
SECOND = "https://covers.example.org/numilog-other.jpg"


def paris_account(profile="alice", credential="card-a", user="buyer-a"):
    return Account(
        identity_id=profile, provider_id="paris",
        credential_id=credential, unique_id=user, is_primary=False,
    )


def personal_items():
    coords = dict(
        provider="DILICOM", medium="digital",
        record_id="resource-123", title="Un roman",
        source_provider="paris", source_credential="card-a",
        source_account_id="buyer-a", identity_id="alice",
    )
    loan = Loan(id="loan-123", **coords)
    history = HistoryItem(id="history-123", **coords)
    return loan, history


def public(db, *, record="resource-123", cover=PUBLIC):
    source = persist(db, verified_public(
        source_provider="paris", docbase="DILICOM",
        record_id=record, revision_id=None,
        title="Un roman", thumbnail_url=cover,
    ), at=100)
    return persist_exact_public_search_cover(db, source), source


def current_host(account, loan, history):
    return NS(
        service=NS(
            current_identity=NS(identity_id=account.identity_id),
            current_account=account, _account_context_epoch=3,
            _source_context_epoch=7,
        ),
        loan_objects={loan_source_key(loan): loan},
        history_objects={history.id: history},
    )


def stamp(db, items, account=None):
    attach_paris_personal_public_c(
        db, items, account=account or paris_account(),
        account_epoch=3, source_epoch=7,
    )


def test_paris_two_personal_views_share_exact_public_c_n2_without_rights(
    tmp_path,
):
    db = Storage(tmp_path / "paris-c.sqlite")
    try:
        canonical, _ = public(db)
        loan, history = personal_items()
        account = paris_account()
        stamp(db, [loan, history], account)
        host = current_host(account, loan, history)
        first = current_paris_personal_public_c(host, loan)
        second = current_paris_personal_public_c(host, history)
        assert type(first) is type(second) is VerifiedParisPersonalPublicC
        assert first is loan._core_notice_c_paris_public_link
        assert first.document == second.document == canonical.document
        assert public_c_is_current(host, first)
        assert public_c_is_current(host, second)
        assert cached_notice_cover_snapshot(
            NS(storage=db), first.document,
        ).candidates[0].url == PUBLIC
        assert db.entitlements() == [] and db.acquisitions() == []

        # The display is a distinct Loan copy; original provider actions
        # still receive the same source item, while the image is from N2.
        events = []
        panel = NS(show_loan_public_c=lambda item, *, public_link: events.append(
            (item, public_link),
        ))
        host.loan_page = NS(detail=panel)
        assert show_paris_public_loan_cover(
            host, loan, lambda: replace(loan),
        )
        assert len(events) == 1
        assert events[0][0] is not loan
        assert events[0][0].record_id == loan.record_id
        assert events[0][1] is first
        assert db.entitlements() == []
    finally:
        db.close()


def test_public_multi_source_outweighs_own_c_with_exact_paris_record(tmp_path):
    db = Storage(tmp_path / "multi-paris.sqlite")
    try:
        own, paris_source = public(db)
        numilog = persist(db, verified_public(
            source_provider="numilog-catalog", record_id="edition-987",
            title="Un roman", thumbnail_url=SECOND,
        ), at=100)
        persist_exact_public_search_cover(db, numilog)
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-paris-numilog",
            source=paris_source, relation_proof_id="strong:fixture:paris",
        )
        shared = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-paris-numilog",
            source=numilog, relation_proof_id="strong:fixture:numilog",
        )
        loan, history = personal_items()
        stamp(db, [loan, history])
        assert loan._core_notice_c_paris_public_link.document == shared.document
        assert history._core_notice_c_paris_public_link.document == shared.document
        assert shared.document != own.document
        assert {x.url for x in cached_notice_cover_snapshot(
            NS(storage=db), shared.document,
        ).candidates} == {PUBLIC, SECOND}
        assert db.entitlements() == []
    finally:
        db.close()


@pytest.mark.parametrize("mutation", [
    {"provider": "SYRACUSE"},
    {"medium": "physical"},
    {"source_provider": "bnf"},
    {"source_credential": "card-b"},
    {"source_account_id": "buyer-b"},
    {"identity_id": "bob"},
    {"record_id": "different-edition"},
    {"record_id": None},
    {"record_id": "https://signed.example.org/token"},
])
def test_other_provider_private_account_and_record_disallowed(tmp_path, mutation):
    db = Storage(tmp_path / "no-laundering.sqlite")
    try:
        public(db)
        loan, _ = personal_items()
        changed = replace(loan, **mutation)
        stamp(db, [changed])
        assert not hasattr(changed, "_core_notice_c_paris_public_link")
        assert db.entitlements() == []
    finally:
        db.close()


def test_wrong_docbase_revision_or_unobserved_public_c_refuses(tmp_path):
    db = Storage(tmp_path / "no-guess.sqlite")
    try:
        first = persist(db, verified_public(
            source_provider="paris", docbase="SYRACUSE",
            record_id="resource-123", title="Un roman",
            thumbnail_url=PUBLIC,
        ), at=100)
        persist_exact_public_search_cover(db, first)
        second = persist(db, verified_public(
            source_provider="paris", docbase="DILICOM",
            record_id="resource-123", revision_id="rev-2",
            title="Un roman", thumbnail_url=PUBLIC,
        ), at=100)
        persist_exact_public_search_cover(db, second)
        loan, history = personal_items()
        stamp(db, [loan, history])
        assert not hasattr(loan, "_core_notice_c_paris_public_link")
        assert not hasattr(history, "_core_notice_c_paris_public_link")
    finally:
        db.close()


def test_replay_is_not_evidence_and_aba_invalidates_all_personal_rows(tmp_path):
    db = Storage(tmp_path / "epoch.sqlite")
    try:
        public(db)
        loan, history = personal_items()
        acc = paris_account()
        stamp(db, [loan, history], acc)
        host = current_host(acc, loan, history)
        current = current_paris_personal_public_c(host, loan)
        assert current is not None
        assert "_core_notice_c_paris_public_link" not in asdict(loan)
        assert "_core_notice_c_paris_public_link" not in asdict(history)
        replay = Loan(**asdict(loan))
        assert not hasattr(replay, "_core_notice_c_paris_public_link")
        assert current_paris_personal_public_c(host, replay) is None

        host.service._account_context_epoch += 1
        assert not public_c_is_current(host, current)
        host.service._account_context_epoch = 3
        host.service._source_context_epoch += 1
        assert not public_c_is_current(host, current)
        host.service._source_context_epoch = 7
        host.service.current_identity.identity_id = "bob"
        assert not public_c_is_current(host, current)
        host.service.current_identity.identity_id = "alice"
        host.loan_objects = {loan_source_key(loan): replace(loan)}
        assert not public_c_is_current(host, current)
        host.loan_objects = {loan_source_key(loan): loan}
        host.service.current_account = paris_account(user="buyer-b")
        assert not public_c_is_current(host, current)
        host.service.current_account = acc

        loan.record_id = "rebound"
        assert not public_c_is_current(host, current)
        assert db.entitlements() == []
    finally:
        db.close()


def test_ambiguous_competing_two_provider_c_never_decorates_personal_dto(tmp_path):
    db = Storage(tmp_path / "competing.sqlite")
    try:
        _, paris_source = public(db)
        a = persist(db, verified_public(
            source_provider="numilog-catalog", record_id="seller-a",
            title="Un roman", thumbnail_url=SECOND,
        ), at=100)
        b = persist(db, verified_public(
            source_provider="bnf", record_id="seller-b",
            title="Un roman", thumbnail_url=SECOND,
        ), at=100)
        for ident, foreign in (("edition:group-a", a), ("edition:group-b", b)):
            persist_consolidated_cover_source(
                db, canonical_edition_id=ident, source=paris_source,
                relation_proof_id="strong:fixture:paris",
            )
            persist_consolidated_cover_source(
                db, canonical_edition_id=ident, source=foreign,
                relation_proof_id="strong:fixture:other",
            )
        loan, history = personal_items()
        stamp(db, [loan, history])
        assert not hasattr(loan, "_core_notice_c_paris_public_link")
        assert not hasattr(history, "_core_notice_c_paris_public_link")
    finally:
        db.close()


def test_actual_cached_loan_worker_recreates_transient_public_c_receipt(tmp_path):
    """The provider GET is deliberately absent: exact account cache replay."""
    from cifereca.services import LibraryService, _loan_cache_key
    db = Storage(tmp_path / "cached-loans.sqlite")
    try:
        own, _ = public(db)
        account = paris_account()
        loan, _ = personal_items()
        db.cache_put("account", _loan_cache_key(account), [asdict(loan)], 3600)
        fake = NS(
            storage=db, config={"cache.dashboard_seconds": 300},
            _sessions={}, _session_key=lambda *_: "unregistered",
            _record_loan_statistics=lambda *_a, **_k: None,
            _account_context_epoch=3, _source_context_epoch=7,
        )
        rows = LibraryService._loans_for_account(fake, account, force=False)
        assert len(rows) == 1
        assert rows[0] is not loan
        assert rows[0]._core_notice_c_paris_public_link.document == own.document
        assert "_core_notice_c_paris_public_link" not in asdict(rows[0])
        assert db.entitlements() == []
    finally:
        db.close()


def test_actual_cached_history_worker_recreates_scope_guarded_c_offline(tmp_path):
    """No provider/history GET or GTK SQL on an already account-scoped cache."""
    from cifereca.core.library_projection import refresh_history, _history_cache_key
    db = Storage(tmp_path / "cached-history.sqlite")
    try:
        own, _ = public(db)
        account = paris_account()
        _, item = personal_items()
        db.cache_put(
            "account", _history_cache_key(account),
            [asdict(item)], 3600,
        )
        fake = NS(
            storage=db, config={"cache.history_seconds": 1800},
            _account_context_epoch=3, _source_context_epoch=7,
            current_account=account,
            diagnostics=NS(add=lambda *_a, **_k: None),
            last_history=[],
        )
        rows = refresh_history(fake, force=False, account=account)
        assert len(rows) == 1
        assert rows[0] is not item
        assert rows[0]._core_notice_c_paris_public_link.document == own.document
        assert fake.last_history == rows
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def _history_production_method(name, *, row_factory=None):
    """Load ONLY the actual MainWindow presenter, no GTK display required."""
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    ns = {
        "__name__": "cifereca.gui.main_window",
        "__package__": "cifereca.gui",
        "BookListItem": row_factory,
        "fr_date": lambda *_: "16/11/2026",
    }
    module = ast.Module(
        body=[ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")],
            level=0,
        ), method],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), ns)
    return ns[name]


def test_history_actual_list_and_selection_use_one_exact_c_without_get(
    tmp_path, monkeypatch,
):
    """The REAL MainWindow methods bind C in list and selected detail."""
    from cifereca.gui import history_cover_presentation
    from cifereca.gui import history_cover_decision_bridge
    from cifereca.gui import history_document_cover_bridge

    monkeypatch.setattr(
        history_cover_decision_bridge,
        "history_cover_presentation", lambda _host, item: item,
    )
    monkeypatch.setattr(
        history_cover_presentation,
        "cached_history_cover", lambda _host, _item: None,
    )
    monkeypatch.setattr(
        history_document_cover_bridge,
        "exact_history_document_evidence", lambda _host, _item: None,
    )

    class Row:
        def __init__(self, key, title, subtitle, meta, badge, thumbnail, **kw):
            self.key, self.title = key, title
            self.subtitle, self.meta, self.badge = subtitle, meta, badge
            self.thumbnail_url = thumbnail
            for k, v in kw.items():
                setattr(self, k, v)

    db = Storage(tmp_path / "real-history-ui.sqlite")
    try:
        c, _ = public(db)
        _loan, history = personal_items()
        account = paris_account()
        stamp(db, [history], account)
        events = []
        detail = NS(
            show_history_public_c=lambda presented, *, public_link: events.append(
                (presented, public_link),
            ),
            show_history=lambda *_: events.append(("legacy",)),
            show_history_document=None,
            empty=lambda *_: events.append(("empty",)),
        )
        host = current_host(account, _loan, history)
        host.history_page = NS(detail=detail)
        host.search_controls = object()
        host._attach_row_facets = lambda *_: None
        host._add_facets = lambda *_: None
        host._add_local_book_actions = lambda *_: None

        build = _history_production_method(
            "_history_book_row", row_factory=Row,
        )
        select = _history_production_method("_history_selected")
        row = build(host, history)
        assert row._cover_document_ref == c.document
        assert row._cover_paris_public_c.document == c.document
        assert row.thumbnail_url is None
        select(host, row)
        assert len(events) == 1
        assert events[0] == (
            history, history._core_notice_c_paris_public_link,
        )
        assert db.entitlements() == []

        host.service._source_context_epoch += 1
        stale = build(host, history)
        assert not hasattr(stale, "_cover_paris_public_c")
        select(host, stale)
        assert events[-1] == ("legacy",)
    finally:
        db.close()
