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


@pytest.mark.parametrize("case", ["exact", "missing", "ambiguous", "wrong_kind", "conflicting",
                                  "wrong_occurrence", "expiring"])
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
            entitlement_id="other-purchase" if case == "wrong_occurrence" else key.right_id,
            identity_id="A", provider_id="seller",
            credential_id="card-A", account_id="buyer-A", item_id=key.edition_id,
            entitlement_type="loan" if case == "wrong_kind" else "owned",
            state="active", expires_at="2099-01-01" if case == "expiring" else None))
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
        from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
        from cifereca.plugin_api.local_origin_detail import LocalOriginPurchaseDeclarationAdvice
        broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: True)
        page = broker.preview(identity_id="A", placement_id=request.placement_id,
                              still_current=lambda: True, confirmed=True)
        selected = next(c for c in page.private_choices if c.kind == "purchase")
        report = broker.compare_private(token=selected.token, confirmed=True)
        hit, = report.matches
        token = hit.declaration_choice
        assert token and token not in repr(hit)
        assert broker.preview_loan_declaration(token=token, confirmed=True) is None
        assert broker.preview_purchase_declaration(token=token) is None
        advice = broker.preview_purchase_declaration(token=token, confirmed=True)
        assert type(advice) is LocalOriginPurchaseDeclarationAdvice
        assert advice.purchase_id == key.right_id and advice.association_authorized is False
        assert broker.preview_purchase_declaration(token=token, confirmed=True) is None
        assert records(service) == []
        # The same exact typed choice is available in the contextual pane.
        from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver
        controller, dispose, host, active, tasks, actions, groups, toasts = mounted(
            (service, session, request, calls, path))
        host.retire_action = lambda name: actions.pop(name, None) is not None
        assert actions["preview"][2]()
        deliver(tasks)
        purchase_action = next(v[2] for k, v in actions.items()
                               if k.startswith("private-") and "achats" in v[0])
        assert purchase_action()
        deliver(tasks)
        assert "Achat : " + key.right_id in repr(groups[-1])
        selected_action = actions["declare-preview-1"][2]
        assert selected_action()
        deliver(tasks)
        assert not selected_action()
        assert any(g[0] == "Aperçu de déclaration utilisateur"
                   and ("Achat", key.right_id) in g[1] for g in groups)
        assert not toasts and records(service) == []
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
