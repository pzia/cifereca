"""P0 #6366: borrower source-record exact identity -> durable public C N2.

The private occurrence/borrower remains independent. A separately attested
PUBLIC Search source, not a title/image/ISBN lookup, supplies the C reference.
All fixtures use real persisted C rows; no provider calls, files or rights.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_borrower_public_projection import (
    VerifiedBorrowerPublicC, attach_borrower_public_c,
    borrower_public_c_receipt,
)
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, persist_consolidated_cover_source,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_cover_repository import _opaque_key
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.gui.borrower_public_c_bridge import (
    admit_borrower_public_c_cell, exact_history_public_c,
    exact_loan_public_c, show_public_history_c, show_public_loan_c,
)
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.models import HistoryItem, Loan
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


URL_A = "https://covers.example.org/edition-oeuvre-a.jpg"
URL_B = "https://covers.example.org/edition-oeuvre-b.jpg"


def bibliography(db, *, provider="bnf-pret", record="book-42",
                 title="Livre source", docbase="BNF_PRET",
                 thumbnail=URL_A, author="Autrice", revision=None):
    row = verified_public(
        source_provider=provider, record_id=record, title=title,
        docbase=docbase, thumbnail_url=thumbnail,
        author=author, revision_id=revision,
    )
    saved = persist(db, row, at=100)
    assert saved.observation.parser_version == "2"
    c = persist_exact_public_search_cover(db, saved)
    return saved, c


def loan(**changed):
    item = Loan(
        id="loan-88", provider="BNF_PRET", medium="digital",
        record_id="book-42", title="Livre source", author="Autrice",
        thumbnail_url=None, source_provider="bnf-pret",
        source_credential="card-a", source_account_id="reader-a",
        identity_id="alice",
    )
    for name, value in changed.items():
        setattr(item, name, value)
    return item


def history(**changed):
    item = HistoryItem(
        id="history-88", provider="BNF_PRET", medium="digital",
        record_id="book-42", title="Livre source", author="Autrice",
        thumbnail_url=None,
        # A provider/default picture is not a direct documentary source.
        default_thumbnail_url="https://covers.example.org/generic-default.png",
        source_provider="bnf-pret", source_credential="card-a",
        source_account_id="reader-a", identity_id="alice",
    )
    for name, value in changed.items():
        setattr(item, name, value)
    return item


def admit(db, rows, *, identity_id="alice"):
    # All mock borrower reads were admitted at these exact Core generations.
    return attach_borrower_public_c(
        db, rows, identity_id=identity_id,
        account_epoch=7, source_epoch=9,
    )


def host_for(book, *, identity="alice"):
    service = NS(
        current_identity=NS(identity_id=identity),
        _account_context_epoch=7, _source_context_epoch=9,
    )
    host = NS(service=service, _history_cover_revision=3)
    if type(book) is Loan:
        host.loan_objects = {loan_source_key(book): book}
        host.loan_page = NS(detail=NS())
    else:
        host.history_objects = {book.id: book}
        host.history_page = NS(detail=NS())
    return host


def cell(host, book, packet):
    source_key = loan_source_key(book) if type(book) is Loan else book.id
    return NS(
        key=source_key, _cover_document_ref=packet.document,
        _cover_borrower_public_c_receipt=packet,
        _borrower_public_c_source=book,
        _borrower_public_c_context=("alice", 7, 9),
        _history_cover_revision=host._history_cover_revision,
    )


def test_loan_history_two_distinct_occurrences_share_exact_public_c_n2(tmp_path):
    filename = tmp_path / "borrower-notices.sqlite"
    db = Storage(filename)
    try:
        observed, c = bibliography(db)
        borrowed, returned = loan(), history()
        assert admit(
            db, [borrowed, returned], identity_id="alice",
        ) == 2
        a = borrower_public_c_receipt(borrowed, identity_id="alice")
        b = borrower_public_c_receipt(returned, identity_id="alice")
        assert type(a) is type(b) is VerifiedBorrowerPublicC
        assert a.document == b.document == c.document
        assert a.epoch == b.epoch == c.epoch
        assert a.source_ref == b.source_ref == observed.document.source_key

        for original, packet in ((borrowed, a), (returned, b)):
            host = host_for(original)
            assert (exact_loan_public_c(host, original)
                    if type(original) is Loan else
                    exact_history_public_c(host, original)) is packet
            row = cell(host, original, packet)
            assert admit_borrower_public_c_cell(host, row, c.document)
            # A list cell may bind N2 without an account-specific N3 GET.
            snap = cached_notice_cover_snapshot(NS(storage=db), c.document)
            assert snap.scope == c.document
            assert [x.url for x in snap.candidates] == [URL_A]

            presented = []
            if type(original) is Loan:
                host.loan_page.detail.show_loan_public_c = (
                    lambda item, *, document:
                    presented.append((item, document))
                )
                assert show_public_loan_c(host, original, lambda: original)
            else:
                host.history_page.detail.show_history_public_c = (
                    lambda item, *, document:
                    presented.append((item, document))
                )
                assert show_public_history_c(host, original, original)
            assert presented == [(original, c.document)]
        assert db.entitlements() == [] and db.acquisitions() == []

        # Cache eviction + process restart: the C candidate and its
        # bibliographic source evidence survive without the source row.
        with db._lock, db._connect() as sql:
            sql.execute(
                "DELETE FROM metadata WHERE key=?",
                (_opaque_key(observed.document),),
            )
        assert cached_notice_cover_snapshot(NS(storage=db), c.document).scope == c.document
    finally:
        db.close()
    restarted = Storage(filename)
    try:
        assert admit(
            restarted, [loan(), history()], identity_id="alice",
        ) == 2
        assert [x.url for x in
                read_consolidated_notice_cover(restarted, c.document).candidates] == [URL_A]
    finally:
        restarted.close()


def test_verified_two_provider_c_selected_over_own_source_without_cross_join(tmp_path):
    db = Storage(tmp_path / "two-public-sources.sqlite")
    try:
        a, own = bibliography(db)
        b, _ = bibliography(
            db, provider="numilog", record="other-source-42",
            thumbnail=URL_B,
        )
        # In production a separately admitted strong relation writes the
        # shared C; this view adapter can READ but cannot create the link.
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:validated-two-sources",
            source=a, relation_proof_id="strong:fixture:origin-a",
        )
        consolidated = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:validated-two-sources",
            source=b, relation_proof_id="strong:fixture:origin-b",
        )
        assert consolidated.document != own.document
        l, h = loan(), history()
        assert admit(db, [l, h], identity_id="alice") == 2
        assert borrower_public_c_receipt(
            l, identity_id="alice",
        ).document == consolidated.document
        assert borrower_public_c_receipt(
            h, identity_id="alice",
        ).document == consolidated.document
        assert {x.url for x in cached_notice_cover_snapshot(
            NS(storage=db), consolidated.document,
        ).candidates} == {URL_A, URL_B}
        assert db.entitlements() == []
    finally:
        db.close()


@pytest.mark.parametrize("change", [
    {"source_provider": "numilog"},
    {"record_id": "other-book"},
    {"medium": "physical"},
    {"identity_id": "bob"},
    {"title": "Ouvrage homonyme mais différent"},
    {"author": "Une autre autrice"},
    {"thumbnail_url": "https://covers.example.org/own-loan.jpg"},
])
def test_no_source_match_or_direct_source_cannot_borrow_public_c(
    tmp_path, change,
):
    db = Storage(tmp_path / "unmatched.sqlite")
    try:
        bibliography(db)
        for factory in (loan, history):
            item = factory(**change)
            assert admit(
                db, [item], identity_id="alice",
            ) == 0
            assert borrower_public_c_receipt(
                item, identity_id="alice",
            ) is None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()



def test_unequal_conflicting_multisource_groups_never_choose_borrower_cover(tmp_path):
    """A 3-source C does not defeat a contradictory 2-source edition claim."""
    db = Storage(tmp_path / "borrower-conflicting-editions.sqlite")
    try:
        a, own = bibliography(db)
        original_loan, original_history = loan(), history()
        # Before any conflicting relation, the exact public self-C is valid.
        assert admit(db, [original_loan, original_history]) == 2
        for original in (original_loan, original_history):
            assert borrower_public_c_receipt(
                original, identity_id="alice",
            ).document == own.document
        b, _ = bibliography(
            db, provider="numilog", record="source-b", thumbnail=URL_B,
        )
        c, _ = bibliography(
            db, provider="paris", record="source-c", thumbnail=URL_B,
        )
        d, _ = bibliography(
            db, provider="other-provider", record="source-d", thumbnail=URL_B,
        )
        large = None
        for source in (a, b, c):
            large = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:conflict-three",
                source=source, relation_proof_id="strong:fixture:large",
            )
        small = None
        for source in (a, d):
            small = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:conflict-two",
                source=source, relation_proof_id="strong:fixture:small",
            )
        assert len(large.sources) == 3
        assert len(small.sources) == 2
        assert len({own.document, large.document, small.document}) == 3
        # Reproject the SAME private DTOs after two contradictory public
        # canonical groups appear. Previously admitted N2 must be revoked.
        assert admit(db, [original_loan, original_history]) == 0
        for original in (original_loan, original_history):
            assert borrower_public_c_receipt(
                original, identity_id="alice",
            ) is None
        # A later refresh must also revoke an old, formerly valid packet:
        # no stale thumbnail or borrowed right may survive re-projection.
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()

def test_record_reused_for_two_docbases_never_associates_without_docbase(tmp_path):
    db = Storage(tmp_path / "docbases.sqlite")
    try:
        bibliography(db, docbase="digital-books")
        bibliography(
            db, docbase="other-catalogue", thumbnail=URL_B,
        )
        assert admit(
            db, [loan(), history()], identity_id="alice",
        ) == 0
    finally:
        db.close()


def test_stale_borrower_receipt_rejected_for_account_epoch_and_rebound(tmp_path):
    db = Storage(tmp_path / "stale.sqlite")
    try:
        bibliography(db)
        for maker in (loan, history):
            original = maker()
            assert admit(
                db, [original], identity_id="alice",
            ) == 1
            packet = borrower_public_c_receipt(original, identity_id="alice")
            host = host_for(original)
            row = cell(host, original, packet)
            assert admit_borrower_public_c_cell(
                host, row, packet.document,
            )
            host.service._account_context_epoch += 1
            assert not admit_borrower_public_c_cell(
                host, row, packet.document,
            )
            host.service._account_context_epoch -= 1
            host.service._source_context_epoch += 1
            assert not admit_borrower_public_c_cell(
                host, row, packet.document,
            )
            host.service._source_context_epoch -= 1
            clone = replace(original)
            assert borrower_public_c_receipt(
                clone, identity_id="alice",
            ) is None
            original.source_credential = "other-card"
            assert borrower_public_c_receipt(
                original, identity_id="alice",
            ) is None
        assert db.entitlements() == []
    finally:
        db.close()


def test_same_public_cover_is_not_right_and_stale_receipt_is_revoked(tmp_path):
    db = Storage(tmp_path / "replay.sqlite")
    try:
        bibliography(db)
        x = loan()
        assert admit(db, [x], identity_id="alice") == 1
        assert borrower_public_c_receipt(x, identity_id="alice") is not None
        x.thumbnail_url = URL_A  # provider now supplies its own direct cover
        assert admit(db, [x], identity_id="alice") == 0
        assert borrower_public_c_receipt(x, identity_id="alice") is None
        assert not x.__dict__.get("_core_public_c_cover_receipt")
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_gtk_wiring_stays_worker_only_and_keeps_actions(tmp_path):
    # Stable AST contract: no DB read or supplier GET from Borrower list/
    # detail. The outgoing private loan/return workflows are unchanged.
    root = Path(__file__).resolve().parents[1] / "src/cifereca"
    loan_code = (root / "gui/loans_controller.py").read_text()
    history_code = (root / "gui/main_window.py").read_text()
    panel = (root / "gui/detail_panel.py").read_text()
    source = (root / "core/library_projection.py").read_text()
    assert "attach_borrower_public_c(" in loan_code.split("def work():", 1)[1]
    assert "_history_source_lease_current(" in source.split("def refresh_history(", 1)[1]
    assert "exact_loan_public_c(self, loan)" in loan_code
    assert "show_public_loan_c(" in loan_code
    assert "exact_history_public_c(self, x)" in history_code
    assert "show_public_history_c(self, item, presented)" in history_code
    assert "def show_loan_public_c(" in panel
    assert "def show_history_public_c(" in panel
    assert "self.show_loan(loan)" in panel
    assert "self.show_history(item)" in panel


@pytest.mark.parametrize("source_provider,branch", [
    ("paris", "DILICOM"),
    ("bnf-pret", "BNF_PRET"),
])
def test_same_native_record_must_match_its_public_docbase_and_revision(
    tmp_path, source_provider, branch,
):
    db = Storage(tmp_path / "native-branch-document-identity.sqlite")
    try:
        record = "book-42"
        correct = {
            "source_provider": source_provider, "provider": branch,
        }
        item_loan = loan(**correct)
        item_history = history(**correct)
        # Exactly the same upstream book record and title in another
        # public docbase C is NOT the attested edition.
        bibliography(db, provider=source_provider, record=record,
                     docbase="WRONG_BRANCH")
        assert admit(db, [item_loan, item_history]) == 0
        # A provider revision on the public source is NOT attested by the
        # existing private Loan/History DTO shape.
        bibliography(db, provider=source_provider, record=record,
                     docbase=branch, revision="revision-2")
        assert admit(db, [item_loan, item_history]) == 0
        # Add the native docbase without a revision. It is the only
        # admissible edition regardless of homonymous competing sources.
        observed, expected = bibliography(
            db, provider=source_provider, record=record,
            docbase=branch, revision=None,
        )
        assert admit(db, [item_loan, item_history]) == 2
        for item in (item_loan, item_history):
            packet = borrower_public_c_receipt(item, identity_id="alice")
            assert packet is not None
            assert packet.document == expected.document
            assert packet.source_ref == observed.document.source_key
        # A later supplier branch swap must invalidate an old packet even
        # when the record/title/medium remain byte-identical.
        item_loan.provider = "UNRELATED_BRANCH"
        assert borrower_public_c_receipt(
            item_loan, identity_id="alice",
        ) is None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("source_provider,branch", [
    ("paris", "SYRACUSE"),
    ("bnf-pret", "DILICOM"),
    ("other-provider", "BNF_PRET"),
    ("numilog", "DILICOM"),
])
def test_unsupported_native_branch_never_links_a_public_edition(
    tmp_path, source_provider, branch,
):
    db = Storage(tmp_path / "unattested-provider-branch.sqlite")
    try:
        bibliography(
            db, provider=source_provider, record="book-42",
            docbase=branch,
        )
        for make in (loan, history):
            item = make(source_provider=source_provider, provider=branch)
            assert admit(db, [item]) == 0
            assert borrower_public_c_receipt(
                item, identity_id="alice",
            ) is None
    finally:
        db.close()


def test_positive_paris_branch_attested_relation_prefers_multisource_c(tmp_path):
    db = Storage(tmp_path / "paris-multi-provider-edition.sqlite")
    try:
        source, own = bibliography(
            db, provider="paris", record="book-42", docbase="DILICOM",
        )
        second, _ = bibliography(
            db, provider="numilog", record="different-record",
            docbase="NUMILOG", thumbnail=URL_B,
        )
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:paris-verified-pair",
            source=source, relation_proof_id="strong:fixture:paris",
        )
        combined = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:paris-verified-pair",
            source=second, relation_proof_id="strong:fixture:numilog",
        )
        assert combined.document != own.document
        item = history(source_provider="paris", provider="DILICOM")
        assert admit(db, [item]) == 1
        packet = borrower_public_c_receipt(item, identity_id="alice")
        assert packet is not None
        assert packet.document == combined.document
        assert {x.url for x in cached_notice_cover_snapshot(
            NS(storage=db), combined.document,
        ).candidates} == {URL_A, URL_B}
        assert db.entitlements() == []
    finally:
        db.close()
