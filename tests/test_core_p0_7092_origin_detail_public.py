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
        ("one", "isbn_title_author_concordant"), ("two", "isbn_claim_ambiguous")]
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
