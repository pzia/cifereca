"""#6168: attested loan supplier covers bind N2 in list and detail."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_projection import ResolvedBookCover
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.loan_supplier_document_cover_bridge import (
    exact_supplier_loan_cover,
    register_exact_loan_supplier,
    show_supplier_loan_cover,
)
from cifereca.models import Loan


ROOT = Path(__file__).resolve().parents[1]
URL = "https://example.org/a.png"


@pytest.fixture
def owner():
    loan = Loan(
        id="l1", provider="paris", medium="digital", record_id="rec-1",
        title="Same title", thumbnail_url=URL, identity_id="alice",
        source_provider="paris", source_credential="credential-a",
        source_account_id="",
    )
    host = NS(
        service=NS(current_identity=NS(identity_id="alice"),
                   _account_context_epoch=3, _source_context_epoch=5),
        _identity_context_generation=7, _loan_cover_decision_revision=9,
        loan_objects={loan_source_key(loan): loan},
        _closed=False,
    )
    return host, loan


def test_source_loan_is_its_own_exact_catalogue_proof(owner):
    host, loan = owner
    proof = exact_supplier_loan_cover(host, loan, displayed=loan)
    assert proof is not None
    assert proof.document.kind == "loan"
    assert proof.document.identity_id == "alice"
    assert proof.document.credential_id == "credential-a"
    assert proof.document.account_id == ""
    assert proof.document.loan_id == "l1"
    assert proof.candidate.tier == "catalog"
    assert proof.candidate.url == URL
    assert proof.context == (7, 3, 5, 9)


@pytest.mark.parametrize("alteration", [
    {"identity_id": "bob"}, {"source_credential": "credential-b"},
    {"source_provider": "another"}, {"source_account_id": "other"},
    {"id": "other"}, {"record_id": "other"},
])
def test_no_weak_join_from_same_title_or_url(owner, alteration):
    host, loan = owner
    foreign = replace(loan, **alteration)
    assert exact_supplier_loan_cover(host, foreign, displayed=foreign) is None


def test_original_source_object_and_validated_url_are_required(owner):
    host, loan = owner
    clone = replace(loan)
    assert exact_supplier_loan_cover(host, clone, displayed=clone) is None
    assert exact_supplier_loan_cover(
        host, loan, displayed=replace(loan, thumbnail_url="https://other.org/x.png"),
    ) is None
    loan.thumbnail_url = "javascript:evil()"
    assert exact_supplier_loan_cover(host, loan, displayed=loan) is None


def test_aba_epochs_and_mutation_revoke_stale_packet(owner, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    host, loan = owner
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    try:
        proof = exact_supplier_loan_cover(host, loan, displayed=loan)
        assert proof is not None
        assert register_exact_loan_supplier(
            host, loan, service, proof, proof.document,
        )
        first = service._snapshots[proof.document]
        # A->B->A is not an invitation to replay an old producer epoch.
        host.service.current_identity.identity_id = "bob"
        host._identity_context_generation += 1
        assert not register_exact_loan_supplier(
            host, loan, service, proof, proof.document,
        )
        host.service.current_identity.identity_id = "alice"
        host._identity_context_generation += 1
        assert not register_exact_loan_supplier(
            host, loan, service, proof, proof.document,
        )
        renewed = exact_supplier_loan_cover(host, loan, displayed=loan)
        assert renewed is not None
        assert register_exact_loan_supplier(
            host, loan, service, renewed, renewed.document,
        )
        second = service._snapshots[proof.document]
        assert second.revision > first.revision
        # Rebinding the detail does not register or download a new source.
        assert register_exact_loan_supplier(
            host, loan, service, renewed, renewed.document,
        )
        assert service._snapshots[proof.document] is second
        loan.thumbnail_url = "https://example.org/changed.png"
        assert not register_exact_loan_supplier(
            host, loan, service, renewed, renewed.document,
        )
    finally:
        service.close()
        loader.close()


def test_loan_source_new_occurrence_advances_revision(owner, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    host, loan = owner
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    try:
        old = exact_supplier_loan_cover(host, loan, displayed=loan)
        assert old is not None
        assert register_exact_loan_supplier(host, loan, service, old, old.document)
        first_rev = service._snapshots[old.document].revision
        updated = replace(loan, thumbnail_url="https://example.org/new.png")
        host.loan_objects[loan_source_key(updated)] = updated
        host._loan_cover_decision_revision += 1
        assert not register_exact_loan_supplier(
            host, loan, service, old, old.document,
        )
        new = exact_supplier_loan_cover(host, updated, displayed=updated)
        assert new is not None
        assert register_exact_loan_supplier(host, updated, service, new, new.document)
        latest = service._snapshots[old.document]
        assert latest.revision > first_rev
        assert latest.candidates == (new.candidate,)
    finally:
        service.close()
        loader.close()


def test_do_not_override_stronger_or_ambiguous_core_cover(owner, monkeypatch):
    host, loan = owner
    host._loan_cover_projection = (3, "alice", ({},))
    scope = exact_supplier_loan_cover
    from cifereca.gui import loan_cover_decision_bridge as bridge
    document = __import__(
        "cifereca.core.book_cover_source_projection", fromlist=["exact_loan_cover_scope"],
    ).exact_loan_cover_scope(loan)
    assert document is not None
    for status, tier, uri in [
        ("selected", "origin", "https://example.org/origin.png"),
        ("selected", "embedded", "file:///test/cover.img"),
        ("ambiguous", "catalog", ""),
    ]:
        monkeypatch.setattr(
            bridge, "loan_cover_decision",
            lambda *_args, _status=status, _tier=tier, _uri=uri, **_kwargs:
                ResolvedBookCover(
                    document, 9, _status,
                    "conflicting_evidence" if _status == "ambiguous" else "attested",
                    uri=_uri, tier=_tier,
                ),
        )
        assert scope(host, loan, displayed=loan) is None


def test_list_and_detail_share_same_n2_gateway_and_legacy_actions(owner):
    host, loan = owner
    source = (ROOT / "src/cifereca/gui/loans_controller.py").read_text()
    list_ui = (ROOT / "src/cifereca/gui/book_widgets.py").read_text()
    detail = (ROOT / "src/cifereca/gui/detail_panel.py").read_text()
    assert "item._cover_document_ref = supplier.document" in source
    assert "item._cover_loan_supplier_evidence = supplier" in source
    assert "item._loan_source_item = loan" in source
    assert "register_exact_loan_supplier(" in list_ui
    assert "register_exact_loan_supplier(" in detail
    assert "variant=\"list\"" in list_ui
    assert "variant=\"detail\"" in detail
    assert "show_supplier_loan_cover(" in source
    assert "self.loan_page.detail.show_loan(_loan_display_cover(self, loan))" in source
    assert "self.loan_page.detail.show_loan(_loan_display_cover(self, fresh))" in source


def test_detail_adapter_stages_source_only_when_supported(owner):
    host, loan = owner
    calls = []
    host.loan_page = NS(detail=NS(show_loan_local=lambda *args, **kwargs:
                                   calls.append((args, kwargs))))
    assert show_supplier_loan_cover(host, loan, lambda: loan)
    assert len(calls) == 1
    assert calls[0][0] == (loan,)
    assert calls[0][1]["cover_evidence"].document.kind == "loan"
    host._loan_cover_decision_revision += 1
    host.service.current_identity.identity_id = "bob"
    assert not show_supplier_loan_cover(host, loan, lambda: loan)
    assert len(calls) == 1
