"""Scoped typed/legacy loan reads survive UI changes without stale rendering."""
from types import SimpleNamespace as NS

import pytest

from cifereca.services import _loan_cache_key
from tests.test_sync_state_contract import _service
from tests.test_core_p1_3119_3122_opening_typed_inventory import account, loan, snapshot


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("entrypoint", ["direct", "source_sync", "account_ui", "identity_ui"])
@pytest.mark.parametrize("round_trip", [False, True])
def test_source_observation_is_cached_exactly_and_old_ui_never_replaces_new_projection(
    tmp_path, monkeypatch, typed, entrypoint, round_trip,
):
    service = _service(tmp_path)
    a = account(identity="default", credential="card-A", borrower="reader-A")
    b = account(identity="alex", credential="card-B", borrower="reader-B")
    service.accounts = [a, b]
    observed = loan(a)
    calls = []
    visible = []
    monkeypatch.setattr(service, "refresh_adept_inventory", lambda *args: {})
    monkeypatch.setattr("cifereca.core.sync_coordination.acquisition_lock_file",
                        lambda: tmp_path / "source.lock")

    def read(requested):
        assert requested is a
        calls.append(requested)
        service.select_identity("alex")
        if round_trip:
            service.select_identity("default")
        visible[:] = [loan(service.current_account, suffix="fresh-visible")]
        service.last_loans = list(visible)
        return snapshot(a, [observed]) if typed else [observed]

    session = NS(provider_id="paris", credential_id=a.credential_id,
                 connected=True, accounts=[a], close=lambda: None,
                 capabilities=NS(loans=True, personal_library=False))
    if typed:
        session.loan_inventory_snapshot = read
        session.loans = lambda *_: pytest.fail("no second legacy read")
    else:
        session.loans = read
    service._sessions[service._session_key("paris", a.credential_id)] = session
    service.select_identity("default")
    generation = service._source_context_epoch
    try:
        if entrypoint == "source_sync":
            result = service.synchronize_account_source(a)
            assert result["loans"] == 1
            assert {(r["identity_id"], r["credential_id"])
                    for r in service.storage.entitlements()} == {("default", "card-A")}
        else:
            if entrypoint == "direct":
                result = service._loans_for_account(a, force=True)
            elif entrypoint == "account_ui":
                result = service.refresh_loans(force=True)
            else:
                result = service.refresh_identity_loans(force=True, identity_id="default")
            assert result == [observed]
            assert not service.storage.entitlements()
        assert calls == [a]
        assert service.last_loans == visible
        assert service.current_identity.identity_id == ("default" if round_trip else "alex")
        assert service._source_context_epoch == generation
        assert service.storage.cache_get("account", _loan_cache_key(a))[0]["id"] == observed.id
        assert service.storage.cache_get("account", _loan_cache_key(b)) is None
        assert service.storage.acquisitions() == service.storage.placements() == []
    finally:
        service.close()
