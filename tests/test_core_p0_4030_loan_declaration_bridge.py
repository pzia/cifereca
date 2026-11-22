"""#4030 -> #2797: an exact private comparison can only declare an existing right."""
from dataclasses import replace

import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core import local_origin_declaration_bridge as bridge
from cifereca.core import local_origin_private_catalogue as private
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store
from tests.test_library_declared_links import records, rows


@pytest.fixture
def case(private_rig, tmp_path, monkeypatch):
    service, request, account, session, calls, path, key, snap = private_rig
    service.identities = [service.current_identity]
    service.registry = object()
    service.config.data = {}
    monkeypatch.setattr(engine, "acquisition_lock_file", lambda: tmp_path / "declare.lock")
    right = dict(entitlement_id="known-loan", identity_id="A", provider_id="seller",
                 credential_id="card-A", account_id="buyer-A", item_id=key.edition_id,
                 entitlement_type="loan", state="active", loan_id=key.right_id)
    service.storage.entitlement_put(right)
    known = run_local_origin_suggestion(service, request)
    choice, = private.available_known_private_catalogues(
        service, request, evidence=known["public_comparison_evidence"])
    comparison = private.compare_known_private_catalogue(
        service, request, account=account, kind="loan", choice=choice, confirmed=True)
    candidate, = comparison["candidates"]
    return service, request, choice, candidate, right, path, calls


def _preview(case, **options):
    service, request, choice, candidate, *_ = case
    return bridge.preview_private_loan_declaration(
        service, **{"choice": choice, "candidate": candidate, "confirmed": True, **options})


def test_exact_loan_suggestion_uses_same_durable_reversible_writer(case):
    service, request, choice, candidate, right, path, calls = case
    names = ("entitlements", "source_references", "origin_links", "artifacts",
             "placements", "catalog_entries", "acquisition_ledger")
    before = {name: rows(service, name) for name in names}
    data = path.read_bytes()
    assert _preview(case, confirmed=False) is None
    preview = _preview(case)
    assert preview is not None and records(service) == []
    assert preview.context["loan"] == candidate.source.resource_id
    assert preview.context["path"] == str(path)
    assert preview.context["trust"] == "user_declaration"
    assert str(path) not in repr(preview)
    assert bridge.admit_private_loan_declaration(service, preview) is None
    command = bridge.admit_private_loan_declaration(service, preview, user_confirmed=True)
    assert callable(command)
    result = command()
    assert result.status == "declared" and result.changed == 1
    assert command() is None
    record, = records(service)
    assert record["scope"]["item_id"] == candidate.source.edition_id
    assert record["right_state"]["loan_id"] == candidate.source.resource_id
    assert record["origin"] == "user_declaration"
    options = bridge._options(candidate, preview.observation)
    assert engine.inspect_declared_link(service, relation_id=result.relation_id,
                                       **options).status == "declared_current"
    undo = engine.declare_local_link(service, **options, revoke=result.relation_id)
    assert undo.status == "revoke_preview"
    assert engine.declare_local_link(service, **options, revoke=result.relation_id,
                                     confirmation=undo.plan_token).status == "revoked"
    assert records(service)[0]["state"] == "revoked"
    assert {name: rows(service, name) for name in names} == before
    assert path.read_bytes() == data and calls == []


@pytest.mark.parametrize("change", ["absent", "wrong_cycle", "expired", "ambiguous",
                                    "forged_description", "wrong_branch", "cancelled"])
def test_no_declaration_from_documentary_source_without_exact_existing_right(case, change):
    service, request, choice, candidate, right, path, calls = case
    options = {}
    if change == "absent":
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM entitlements")
    elif change in ("wrong_cycle", "expired"):
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE entitlements SET loan_id='other'" if change == "wrong_cycle"
                       else "UPDATE entitlements SET state='expired'")
    elif change == "ambiguous":
        service.storage.entitlement_put({**right, "entitlement_id": "other-loan",
                                         "loan_id": "second-cycle"})
    elif change == "forged_description":
        options["candidate"] = replace(candidate, author="other author")
    elif change == "wrong_branch":
        options["candidate"] = replace(candidate, source=replace(candidate.source, branch="other"))
    else:
        options["still_current"] = lambda: False
    assert _preview(case, **options) is None
    assert records(service) == [] and calls == []


@pytest.mark.parametrize("change", ["sqlite", "source_epoch", "navigation", "same_bytes_new_inode",
                                    "account", "cancelled"])
def test_admitted_command_refuses_drift_before_writer(case, change):
    service, request, choice, candidate, right, path, calls = case
    active = [True]
    preview = _preview(case)
    assert preview is not None
    command = bridge.admit_private_loan_declaration(
        service, preview, user_confirmed=True, still_current=lambda: active[0])
    assert callable(command)
    if change == "sqlite":
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE source_references SET state='inactive'")
    elif change == "source_epoch":
        service._source_context_epoch += 1
    elif change == "navigation":
        service._account_context_epoch += 2
    elif change == "same_bytes_new_inode":
        data = path.read_bytes()
        path.rename(path.with_suffix(".old"))
        path.write_bytes(data)
    elif change == "account":
        service.accounts = [replace(choice.account)]
    else:
        active[0] = False
    assert command() is None and command() is None
    assert records(service) == [] and calls == []


@pytest.mark.parametrize("change", ["none", "cancel", "activation", "view", "right", "new_preview"])
def test_loan_comparison_candidate_token_projects_only_exact_declaration_advice(case, change):
    from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
    from cifereca.plugin_api.local_origin_detail import LocalOriginDeclarationAdvice
    service, request, choice, candidate, right, path, calls = case
    active, visible = [True], [True]
    broker = LocalOriginDetailReadFactory(service).for_activation(
        "fixture", current=lambda: active[0])
    def review():
        return broker.preview(identity_id="A", placement_id=request.placement_id,
                              still_current=lambda: visible[0], confirmed=True)
    page = review()
    loan_choice = next(c for c in page.private_choices if c.kind == "loan")
    report = broker.compare_private(token=loan_choice.token, confirmed=True)
    hit, = report.matches
    token = hit.declaration_choice
    assert type(token) is str and len(token) == 64
    assert broker.preview_loan_declaration(token=token) is None
    assert broker.preview_loan_declaration(token="forged", confirmed=True) is None
    if change == "cancel":
        broker.cancel(token=page.token)
    elif change == "activation":
        active[0] = False
    elif change == "view":
        visible[0] = False
    elif change == "right":
        service.storage.entitlement_put({**right, "state": "expired"})
    elif change == "new_preview":
        assert review() is not None
    before, data = records(service), path.read_bytes()
    advice = broker.preview_loan_declaration(token=token, confirmed=True)
    if change == "none":
        assert type(advice) is LocalOriginDeclarationAdvice
        assert (advice.edition_id, advice.loan_id) == (candidate.source.edition_id, candidate.source.resource_id)
        assert advice.trust == "user_declaration" and not advice.association_authorized
        assert not hasattr(advice, "plan_token") and not hasattr(advice, "path")
    else:
        assert advice is None
    assert broker.preview_loan_declaration(token=token, confirmed=True) is None
    assert records(service) == before and path.read_bytes() == data and calls == []


@pytest.mark.parametrize("change", ["none", "expired", "view", "back"])
def test_detail_loan_declaration_preview_is_explicit_exact_and_read_only(case, change):
    from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver
    service, request, choice, candidate, right, path, calls = case
    controller, dispose, host, active, tasks, actions, groups, toasts = mounted(
        (service, None, request, calls, path))
    host.retire_action = lambda key: actions.pop(key, None) is not None
    add = host.add_action
    def limited(key, title, tooltip, action):
        assert len(actions) < 12
        add(key, title, tooltip, action)
    host.add_action = limited
    before, data = records(service), path.read_bytes()
    assert actions["preview"][2]()
    deliver(tasks)
    action = next(v[2] for k, v in actions.items() if k.startswith("private-"))
    assert action()
    deliver(tasks)
    select = actions["declare-preview-1"][2]
    assert candidate.source.resource_id in repr(groups[-1])
    assert records(service) == before
    if change == "back":
        assert actions["declare-back"][2]()
        deliver(tasks)
        assert not select()
    else:
        if change == "expired":
            service.storage.entitlement_put({**right, "state": "expired"})
        assert select()
        if change == "view":
            host.live = False
        deliver(tasks)
        assert not select()
    assert any(g[0] == "Aperçu de déclaration utilisateur" for g in groups) == (change == "none")
    assert records(service) == before and path.read_bytes() == data and calls == []
