"""#4030 scoped purchase occurrence cannot be inferred from an edition."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core import local_origin_declaration_bridge as bridge
from cifereca.core import local_origin_private_catalogue as private
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store, new_source
from tests.test_library_declared_links import records, rows


@pytest.mark.parametrize("case", ["exact", "wrong_occurrence", "ambiguous", "loan", "conflicting"])
def test_purchase_declaration_requires_exact_existing_owned_occurrence(private_rig, tmp_path, monkeypatch, case):
    service, request, account, session, calls, path, *_ = private_rig
    service.identities = [service.current_identity]
    service.registry = object()
    service.config.data = {}
    monkeypatch.setattr(engine, "acquisition_lock_file", lambda: tmp_path / "declare.lock")
    source, snapshot = new_source(right="purchase-1")
    persist_attested_source_references(service.storage, snapshot)
    right = dict(entitlement_id="purchase-1" if case != "wrong_occurrence" else "other",
                 identity_id="A", provider_id="seller", credential_id="card-A",
                 account_id="buyer-A", item_id=source.edition_id, entitlement_type="owned",
                 state="active")
    if case == "loan":
        right.update(entitlement_type="loan", loan_id="loan-1")
    service.storage.entitlement_put(right)
    if case == "ambiguous":
        service.storage.entitlement_put({**right, "entitlement_id": "second"})
    def read(db, key):
        return NS(facts=(NS(field="title", value="Roman commun", observed_at=100),
                         NS(field="author", value="Autrice", observed_at=100)), alternatives=())
    monkeypatch.setattr(private, "_read_descriptive_txn", read)
    known = run_local_origin_suggestion(service, request)
    choice = next(c for c in private.available_known_private_catalogues(
        service, request, evidence=known["public_comparison_evidence"]) if c.kind == "purchase")
    comparison = private.compare_known_private_catalogue(
        service, request, account=account, kind="purchase", choice=choice, confirmed=True)
    candidate, = comparison["candidates"]
    if case == "conflicting":
        from dataclasses import replace
        candidate = replace(candidate, comparison="purchase_description_conflicting")
    before = {name: rows(service, name) for name in ("entitlements", "source_references", "origin_links", "placements")}
    data = path.read_bytes()
    assert bridge.preview_private_loan_declaration(service, choice=choice, candidate=candidate, confirmed=True) is None
    preview = bridge.preview_private_purchase_declaration(service, choice=choice, candidate=candidate, confirmed=True)
    assert (preview is not None) == (case == "exact")
    if preview is not None:
        assert bridge.admit_private_loan_declaration(service, preview, user_confirmed=True) is None
        assert preview.context["purchase"] == source.right_id
        command = bridge.admit_private_purchase_declaration(service, preview, user_confirmed=True)
        assert callable(command)
        result = command()
        assert result.status == "declared" and command() is None
        assert records(service)[0]["origin"] == "user_declaration"
    else:
        assert records(service) == []
    assert {name: rows(service, name) for name in before} == before
    assert path.read_bytes() == data and calls == []
