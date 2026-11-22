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
