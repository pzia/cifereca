"""#4030: exact existing purchase, never create ownership from C."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core import local_origin_declaration_bridge as bridge
from cifereca.core import local_origin_private_catalogue as private
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store, new_source
from tests.test_library_declared_links import records, rows


@pytest.mark.parametrize("case", ["exact", "missing", "ambiguous", "wrong_kind", "conflicting"])
def test_purchase_declaration_only_for_unique_existing_owned_right(private_rig, tmp_path, monkeypatch, case):
    service, request, account, session, calls, path, _, _ = private_rig
    service.identities = [service.current_identity]
    service.registry = object()
    service.config.data = {}
    monkeypatch.setattr(engine, "acquisition_lock_file", lambda: tmp_path / "declare.lock")
    key, snapshot = new_source()
    persist_attested_source_references(service.storage, snapshot)
    if case == "ambiguous":
        _, other = new_source(right="second-purchase")
        persist_attested_source_references(service.storage, other)
    def reader(db, source):
        facts = [NS(field="title", value="Roman commun", observed_at=100),
                 NS(field="author", value="Autrice", observed_at=100)]
        return NS(facts=tuple(facts), alternatives=())
    monkeypatch.setattr(private, "_read_descriptive_txn", reader)
    if case != "missing":
        service.storage.entitlement_put(dict(
            entitlement_id="purchase-right", identity_id="A", provider_id="seller",
            credential_id="card-A", account_id="buyer-A", item_id=key.edition_id,
            entitlement_type="loan" if case == "wrong_kind" else "owned",
            state="active"))
    known = run_local_origin_suggestion(service, request)
    choice = next(c for c in private.available_known_private_catalogues(
        service, request, evidence=known["public_comparison_evidence"]) if c.kind == "purchase")
    result = private.compare_known_private_catalogue(
        service, request, account=account, kind="purchase", choice=choice, confirmed=True)
    candidate = next(c for c in result["candidates"] if c.source.resource_id == key.right_id)
    if case == "conflicting":
        from dataclasses import replace
        candidate = replace(candidate, comparison="purchase_description_conflicting")
    before = {k: rows(service,k) for k in ("entitlements", "source_references", "origin_links", "placements")}
    data = path.read_bytes()
    preview = bridge.preview_private_purchase_declaration(
        service, choice=choice, candidate=candidate, confirmed=True)
    if case == "exact":
        assert preview is not None and preview.context["purchase"] == key.right_id
        assert bridge.admit_private_loan_declaration(service, preview, user_confirmed=True) is None
        command = bridge.admit_private_purchase_declaration(service, preview, user_confirmed=True)
        assert callable(command) and command().status == "declared" and command() is None
        record, = records(service)
        assert record["origin"] == "user_declaration"
    else:
        assert preview is None and records(service) == []
    assert {k: rows(service,k) for k in before} == before
    assert path.read_bytes() == data and calls == []
