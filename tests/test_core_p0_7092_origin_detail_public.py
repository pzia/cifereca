"""#7092 preserve the explicit anonymous catalogue path across the plugin port."""
import pytest

from cifereca.core.local_origin_detail_broker import (
    LocalOriginDetailReadBrokerImpl, LocalOriginDetailReadFactory,
)
from tests.test_core_p0_4030_public_catalogue import rig, store, rows


def preview(broker, request, view=lambda: True):
    return broker.preview(identity_id=request.identity_id,
                          placement_id=request.placement_id,
                          still_current=view, confirmed=True)


def test_choice_discovery_is_inert_and_explicit_search_is_one_shot(rig):
    service, session, request, calls, path = rig
    before, data = rows(service.storage), path.read_bytes()
    factory = LocalOriginDetailReadFactory(service)
    broker = factory.for_activation("fixture", current=lambda: True)
    page = preview(broker, request)
    choice, = page.public_choices
    assert choice.provider_id == "new-catalogue" and calls == []
    assert broker.search_public(token=choice.token) is None
    assert broker.search_public(token="forged", confirmed=True) is None
    assert calls == []
    report = broker.search_public(token=choice.token, confirmed=True)
    assert report.status == "partial" and report.provider_access_performed
    assert calls == [("Roman commun", "digital", {"field": "title", "content_types": ()})]
    assert [(x.record_id, x.comparison) for x in report.matches] == [
        ("one", "isbn_claim_ambiguous"), ("two", "isbn_claim_ambiguous")]
    assert not (report.association_authorized or report.coverage_complete
                or report.fresh_network_verified)
    assert broker.search_public(token=choice.token, confirmed=True) is None
    assert len(calls) == 1 and rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("change", ["view", "activation", "new_preview", "cancel",
                                    "account", "session", "bytes"])
def test_stale_choice_cannot_dispatch(rig, change):
    service, session, request, calls, path = rig
    live, active = [True], [True]
    broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: active[0])
    page = preview(broker, request, lambda: live[0])
    choice, = page.public_choices
    if change == "view":
        live[0] = False
    elif change == "activation":
        active[0] = False
    elif change == "new_preview":
        assert preview(broker, request).public_choices[0].token != choice.token
    elif change == "cancel":
        broker.cancel(token=page.token)
    elif change == "account":
        session.credentials = {"private": "value"}
    elif change == "session":
        from types import SimpleNamespace
        service._sessions["public"] = SimpleNamespace(**vars(session))
    elif change == "bytes":
        path.write_bytes(b"changed")
    report = broker.search_public(token=choice.token, confirmed=True)
    assert report is None or (not report.provider_access_performed and not report.matches)
    assert calls == []


def test_revocation_during_response_discards_and_never_retries(rig):
    service, session, request, calls, path = rig
    active = [True]
    broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: active[0])
    choice, = preview(broker, request).public_choices
    original = session.search_with_response_facets

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        active[0] = False
        return result

    session.search_with_response_facets = revoked
    assert broker.search_public(token=choice.token, confirmed=True) is None
    assert broker.search_public(token=choice.token, confirmed=True) is None
    assert len(calls) == 1


def test_unscoped_private_reader_has_no_public_authority(rig):
    service, session, request, calls, path = rig
    reader = LocalOriginDetailReadBrokerImpl(service)
    assert preview(reader, request).public_choices == ()
    assert reader.search_public(token="forged", confirmed=True) is None
    assert calls == []


@pytest.mark.parametrize("change", ["same_bytes_new_inode", "new_catalogue_claim", "new_artifact"])
def test_choice_pins_exact_reviewed_file_and_sql_evidence(rig, change):
    import hashlib
    from tests.test_core_p0_2835_epub_layout import _book
    from tests.test_core_p0_4030_epub_evidence import META

    service, session, request, calls, path = rig
    broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: True)
    page = preview(broker, request)
    choice, = page.public_choices
    if change == "same_bytes_new_inode":
        data = path.read_bytes()
        path.rename(path.with_suffix(".old"))
        path.write_bytes(data)
    elif change == "new_catalogue_claim":
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        service.storage.catalog_entry_put({
            "catalog_entry_id": "later-description", "artifact_id": "sha256:" + digest,
            "placement_id": request.placement_id, "identity_id": request.identity_id,
            "title": "Roman commun", "author": "Autrice", "local_state": "local",
        })
    else:
        data = _book(META.replace("Roman commun", "Autre titre privé")).getvalue()
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        artifact = "sha256:" + digest
        service.storage.artifact_put({
            "artifact_id": artifact, "sha256": digest, "size": len(data), "media_format": "epub",
        })
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE placements SET artifact_id=? WHERE placement_id=?",
                       (artifact, request.placement_id))
    report = broker.search_public(token=choice.token, confirmed=True)
    assert report is None or (report.status == "scope_changed"
                              and not report.provider_access_performed and not report.matches)
    assert calls == []
    # A fresh explicit review can approve the new observation; old token cannot.
    assert broker.search_public(token=choice.token, confirmed=True) is None
    new_page = preview(broker, request)
    new_choice, = new_page.public_choices
    assert new_choice.token != choice.token
    new_report = broker.search_public(token=new_choice.token, confirmed=True)
    assert new_report is not None and new_report.provider_access_performed
    assert len(calls) == 1
    assert calls[0][0] == ("Autre titre privé" if change == "new_artifact" else "Roman commun")


@pytest.mark.parametrize("failure,status,label", [
    ("401", "auth_required", "Authentification requise"),
    ("403", "access_denied", "Accès refusé"),
    ("429", "rate_limited", "Limite de requêtes atteinte"),
    ("timeout", "timeout", "Délai de réponse dépassé"),
    ("cancel", "cancelled", "Comparaison annulée"),
    ("transport403", "access_denied", "Accès refusé"),
    ("opaque", "unavailable", "Comparaison publique indisponible"),
])
def test_supplier_failure_is_partial_refusal_not_zero_or_retry(rig, failure, status, label):
    import httpx
    from cifereca.plugin_api import Cancelled, TemporaryUnavailable
    from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver
    service, session, request, calls, path = rig
    before, data = rows(service.storage), path.read_bytes()
    def fail(*args, **kwargs):
        calls.append(args)
        req = httpx.Request("GET", "https://example.invalid/PRIVATE")
        if failure in ("401", "403", "429"):
            raise httpx.HTTPStatusError("PRIVATE", request=req,
                response=httpx.Response(int(failure), request=req))
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE", request=req)
        if failure == "cancel":
            raise Cancelled("PRIVATE")
        if failure == "transport403":
            raise TemporaryUnavailable("transport.http: access_denied")
        raise RuntimeError("transport.http: access_denied PRIVATE")
    session.search_with_response_facets = fail
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(rig)
    assert actions["preview"][2]()
    deliver(tasks)
    search = actions["public-0"][2]
    assert search()
    work, done = tasks[-1]
    result = work()
    assert result.status == status and result.provider_access_performed
    assert result.matches == () and not result.coverage_complete
    assert not result.association_authorized and not result.fresh_network_verified
    done(result)
    done(result)
    assert not search() and len(calls) == 1 and len(groups) == 1
    assert len(toasts) == 1 and label in toasts[0]
    assert "aucune absence attestée" in toasts[0] and "PRIVATE" not in toasts[0]
    assert rows(service.storage) == before and path.read_bytes() == data
