"""#6168 Loan -> exact Core EPUB evidence -> shared N2 list/detail contract."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import epub_metadata
from cifereca.models import Loan
from cifereca.core.loan_cover_evidence import exact_attested_loan_epub_cover
from cifereca.core.loan_cover_fallback import attested_local_loan_cover_proof
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from cifereca.gui.loan_cover_repaint import repaint_exact_loan_cover_rows
from tests.test_epub_cover_contract import _write_epub


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def linked(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    epub = tmp_path / "owned.epub"
    data = _write_epub(epub, epub3=True)
    sha = hashlib.sha256(data).hexdigest()
    facts = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:alice:one",
        artifact_sha256=sha, verified_path=epub,
        artifact_metadata=epub_metadata.inspect_epub(epub),
    )
    assert facts is not None and facts.embedded is not None
    loan = Loan(
        id="loan-one", provider="paris", medium="digital", record_id="item-one",
        title="Shared title", thumbnail_url="https://example.org/legacy.png",
        identity_id="alice", source_provider="paris",
        source_credential="credential-a", source_account_id="",
    )
    row = {
        "key": facts.scope.source_key, "identity_id": "alice", "local": True,
        "artifact_id": facts.scope.artifact_id,
        "placement_id": facts.scope.placement_id,
        "thumbnail_url": loan.thumbnail_url,
        "metadata": {"cover_resolution": {
            "status": "selected", "tier": "catalog", "url": loan.thumbnail_url,
        }},
        "_exact_local_cover_evidence": facts,
    }
    entitlement = {
        "loan_id": loan.id, "credential_id": loan.source_credential,
        "account_id": loan.source_account_id,
    }
    proof = attested_local_loan_cover_proof(
        loan, entitlement, row, identity_id="alice",
        provider_id="paris", item_id=loan.record_id,
    )
    assert proof is not None
    row["_exact_loan_cover_proofs"] = [proof]
    return loan, row, facts


def test_exact_entitlement_proof_prioritizes_epub_even_when_n3_image_is_missing(linked):
    loan, row, facts = linked
    image = epub_metadata.cover_cache_dir() / facts.embedded.resource.cache_name
    image.unlink(missing_ok=True)
    assert exact_attested_loan_epub_cover(loan, (row,)) is facts
    assert not image.exists()
    assert exact_attested_loan_epub_cover(loan, (row, row)) is facts


@pytest.mark.parametrize("mutation", [
    {"identity_id": "bob"},
    {"_library_view_owner": "bob"},
    {"key": "other-document"},
    {"artifact_id": "sha256:" + "a" * 64},
    {"placement_id": "foreign-placement"},
    {"_exact_local_cover_evidence": None},
    {"_exact_loan_cover_proofs": []},
    {"_exact_loan_cover_proofs": [{"loan_id": "loan-one"}]},
    {"metadata": {"cover_resolution": {
        "status": "selected", "tier": "origin",
        "url": "https://example.org/different.png",
    }}},
    {"thumbnail_url": "https://example.org/different.png"},
])
def test_no_cross_profile_or_unproven_placement_join(linked, mutation):
    loan, row, _facts = linked
    assert exact_attested_loan_epub_cover(loan, ({**row, **mutation},)) is None


@pytest.mark.parametrize("change", [
    {"id": "other-loan"},
    {"record_id": "other-item"},
    {"identity_id": "bob"},
    {"source_credential": "credential-b"},
    {"source_account_id": "borrower-b"},
    {"source_provider": "different-provider"},
])
def test_borrower_and_loan_coordinates_must_match(linked, change):
    from dataclasses import replace
    loan, row, _facts = linked
    assert exact_attested_loan_epub_cover(replace(loan, **change), (row,)) is None


def test_no_guess_from_shared_sha_or_title(linked):
    loan, row, facts = linked
    without_rights = {**row, "_exact_loan_cover_proofs": []}
    assert exact_attested_loan_epub_cover(loan, (without_rights,)) is None
    assert exact_attested_loan_epub_cover(loan, tuple()) is None
    assert exact_attested_loan_epub_cover(loan, (row,) * 10001) is None
    # A second, independently attested local Placement cannot be arbitrarily
    # chosen because it shares the same content-addressed EPUB image.
    from dataclasses import replace
    other = replace(facts, scope=replace(facts.scope, placement_id="placement:alice:two"))
    # Rebind embedded Core resources to the second exact Placement.
    other = replace(other, embedded=replace(
        other.embedded, resource=replace(other.embedded.resource, scope=other.scope),
    ))
    row2 = {
        **row, "placement_id": other.scope.placement_id,
        "_exact_local_cover_evidence": other,
        "_exact_loan_cover_proofs": [{
            **row["_exact_loan_cover_proofs"][0],
            "placement_id": other.scope.placement_id,
        }],
    }
    assert exact_attested_loan_epub_cover(loan, (row, row2)) is None


def test_active_account_epoch_guards_production_loan_gtk_admission(linked):
    loan, row, facts = linked
    source = ROOT / "src/cifereca/gui/loans_controller.py"
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef)
                and n.name == "_loan_embedded_cover_evidence")
    scope = dict(Loan=Loan, exact_attested_loan_epub_cover=exact_attested_loan_epub_cover)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")],
                       level=0),
        node,
    ], type_ignores=[])), str(source), "exec"), scope)
    admit = scope["_loan_embedded_cover_evidence"]
    host = NS(
        service=NS(current_identity=NS(identity_id="alice"),
                   _account_context_epoch=4),
        _loan_cover_projection=(4, "alice", (row,)),
    )
    assert admit(host, loan) is facts
    host.service._account_context_epoch += 1
    assert admit(host, loan) is None
    host.service._account_context_epoch -= 1
    host.service.current_identity.identity_id = "bob"
    assert admit(host, loan) is None


def test_list_and_detail_use_same_local_documentref_without_abi_change():
    loans = (ROOT / "src/cifereca/gui/loans_controller.py").read_text()
    detail = (ROOT / "src/cifereca/gui/detail_panel.py").read_text()
    assert "item._cover_document_ref = evidence.scope" in loans
    assert "item._cover_local_evidence = evidence" in loans
    assert "item._public_row_content += (evidence,)" in loans
    assert "present(displayed, cover_evidence=evidence)" in loans
    assert "def show_loan_local(self, loan: Loan, *, cover_evidence)" in detail
    assert "self.show_loan(loan)" in detail
    assert "self._next_cover_evidence = None" in detail
    assert "def show_loan(self, loan: Loan, include_pnb_action: bool = True)" in detail


def test_n2_loan_cell_refuses_url_only_in_place_repaint():
    # An N2 packet is a documentary association, NOT a thumbnail URL update.
    row = NS(key="loan", _loan_cover_context=("alice", 3, 9),
             _public_row_content=("loan", "loan", "T", "A", "m", "b",
                                  "https://example.org/a.png", "style",
                                  ("alice", 3, 9), object()))
    page = NS(store=NS(get_n_items=lambda: 1, get_item=lambda _i: row))
    assert repaint_exact_loan_cover_rows(page, (row,), context=("alice", 3, 9)) is None
