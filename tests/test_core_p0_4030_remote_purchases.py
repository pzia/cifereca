"""Synthetic explicit purchase comparison using existing typed A/C observation."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import local_origin_remote_purchases as remote
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from cifereca.plugin_api import (
    PurchaseEnrichmentCandidate, PurchaseEnrichmentObservation, from_legacy_rights_snapshot,
)
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store, rows, new_source


@pytest.mark.parametrize("case", ["valid", "cursor", "cancelled", "reader_changed", "wrong_scope"])
def test_remote_purchase_is_one_explicit_read_and_never_publishes(
    private_rig, tmp_path, monkeypatch, case,
):
    service, request, account, session, calls, path, *_ = private_rig
    session.capabilities = NS(personal_library=True)
    _, snapshot = new_source()
    inventory = from_legacy_rights_snapshot(snapshot)
    resource, = inventory.resources
    locator = "https://catalog.example.org/product/123"
    candidate = PurchaseEnrichmentCandidate(
        resource.source, locator, "Roman commun", resource.observed_at)
    active = [True]

    def reader(selected):
        assert selected is account
        calls.append("purchase_observation")
        if case == "cancelled":
            active[0] = False
        if case == "wrong_scope":
            _, other = new_source(account="other")
            return PurchaseEnrichmentObservation(from_legacy_rights_snapshot(other))
        value = replace(inventory, resume_cursor="next") if case == "cursor" else inventory
        return PurchaseEnrichmentObservation(value, (candidate,))

    session.purchase_enrichment_observation = reader
    session.rights_observation_snapshot = lambda *_a: pytest.fail("no fallback")
    session.library = session.download = session.search = lambda *_a: pytest.fail("no extra IO")
    monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
    known = run_local_origin_suggestion(service, request)
    choice, = remote.available_remote_purchase_catalogues(
        service, request, evidence=known["public_comparison_evidence"])
    assert not calls
    before, data = rows(service.storage), path.read_bytes()
    assert remote.compare_remote_purchase_catalogue(service, choice)["status"] == "excluded"
    if case == "reader_changed":
        del session.purchase_enrichment_observation
    result = remote.compare_remote_purchase_catalogue(
        service, choice, confirmed=True, still_current=lambda: active[0])
    assert len(calls) == (0 if case == "reader_changed" else 1)
    if case == "valid":
        match, = result["candidates"]
        assert match.edition_id == resource.source.edition_id
        assert match.purchase_id == resource.source.resource_id
        assert match.comparison == "title_concordant_author_unknown"
    else:
        assert not result["candidates"]
        if case == "cursor":
            assert result["status"] == "partial" and result["window_truncated"]
    assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
                                       "association_authorized", "may_write_origin_or_right",
                                       "local_description_transmitted"))
    assert locator not in repr(result)
    assert rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("case", ["valid", "cancel", "activation", "view", "cursor"])
@pytest.mark.parametrize("paged_host", [False, True])
def test_remote_purchase_contextual_broker_and_ui(
    private_rig, tmp_path, monkeypatch, case, paged_host,
):
    from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver

    service, request, account, session, calls, path, *_ = private_rig
    session.capabilities = NS(personal_library=True)
    _, snapshot = new_source()
    inventory = from_legacy_rights_snapshot(snapshot)
    resource, = inventory.resources
    locator = "https://catalog.example.org/product/123"
    candidate = PurchaseEnrichmentCandidate(
        resource.source, locator, "Roman commun", resource.observed_at)

    def reader(selected):
        assert selected is account
        calls.append("purchase_observation")
        value = replace(inventory, resume_cursor="next") if case == "cursor" else inventory
        return PurchaseEnrichmentObservation(value, (candidate,))

    session.purchase_enrichment_observation = reader
    monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(
        (service, session, request, calls, path))
    if paged_host:
        host.retire_action = lambda key: actions.pop(key, None) is not None
    before, data = rows(service.storage), path.read_bytes()
    assert actions["preview"][2]()
    deliver(tasks)
    choice, = c.page.remote_purchase_choices
    assert not calls
    assert c.broker.compare_remote_purchase(token=choice.token) is None
    assert c.broker.compare_remote_loan(token=choice.token, confirmed=True) is None
    action = actions["remote-purchase-0"][2]
    assert action() and not action()
    work, done = tasks[-1]
    if case == "cancel":
        c.broker.cancel(token=c.page.token)
    elif case == "activation":
        active[0] = False
    elif case == "view":
        host.live = False
    value = work()
    done(value)
    if case in ("valid", "cursor"):
        assert calls == ["purchase_observation"]
        assert value.provider_read_requested and not value.association_authorized
        assert not value.coverage_complete and not value.fresh_network_verified
        assert not value.local_description_transmitted
        assert bool(value.matches) == (case == "valid")
        assert value.window_truncated == (case == "cursor")
        assert any(g[0] == "Inventaire privé d’achats — lecture seule" for g in groups)
        assert locator not in repr(groups) and locator not in repr(value)
    else:
        assert not calls and value is None
    assert c.broker.compare_remote_purchase(token=choice.token, confirmed=True) is None
    assert rows(service.storage) == before and path.read_bytes() == data
    dispose()
