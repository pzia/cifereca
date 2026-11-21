"""#7231: classify an exact observed Paris ACSM action before content GET."""
from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p0_3252_paris_download_scope import SIGNED, prepare
from tests.test_p1_3252_paris_selected_right_freshness import raw_loan


def urls():
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(SIGNED)
    stem = p.path.rsplit("/", 1)[0]
    def link(path, query=p.query):
        return urlunsplit((p.scheme, p.netloc, stem + "/" + path, query, ""))
    return link("candidate.acsm"), link("candidate.epub"), link("handler", "format=acsm")


def args(offer):
    return dict(edition_id="edition-1", resource_id="loan-1", kind="loan",
                branch="dilicom", selected_offer_id=offer)


@pytest.mark.parametrize("route,expected", [(0, True), (1, False), (2, False)])
def test_offer_bound_route_is_local_and_does_not_infer_from_media_format(tmp_path, monkeypatch, route, expected):
    session, account, _, client, gets = prepare(tmp_path, monkeypatch)
    row = raw_loan(urls()[route])
    row["AdditionalProperties"]["MediaFormat"] = "ACSM"
    client.digital = [row]
    try:
        offered = session.rights_observation_snapshot(account).rights[0]
        assert offered.delivery_attested
        calls = list(client.calls)
        assert session.downloadable_selected_candidate_is_acsm(account, **args(offered.delivery_offer_id)) is expected
        assert gets == [] and client.calls == calls
        for changes in (dict(resource_id="other"), dict(edition_id="other"),
                        dict(kind="purchase"), dict(branch="foreign")):
            with pytest.raises(ProviderCapabilityError):
                session.downloadable_selected_candidate_is_acsm(
                    account, **{**args(offered.delivery_offer_id), **changes},
                )
        with pytest.raises(ProviderCapabilityError):
            session.downloadable_selected_candidate_is_acsm(
                replace(account, identity_id="foreign"), **args(offered.delivery_offer_id),
            )
        assert gets == [] and client.calls == calls
    finally:
        session.close()


def test_signed_query_rotation_preserves_route_id(tmp_path, monkeypatch):
    session, account, _, client, gets = prepare(tmp_path, monkeypatch)
    acsm, _, _ = urls()
    try:
        client.digital = [raw_loan(acsm)]
        first = session.rights_observation_snapshot(account).rights[0].delivery_offer_id
        client.digital = [raw_loan(acsm + "&rotated=2")]
        second = session.rights_observation_snapshot(account).rights[0].delivery_offer_id
        assert first == second and gets == []
    finally:
        session.close()


@pytest.mark.parametrize("before,after", [(0, 1), (1, 0)])
def test_route_change_in_final_inventory_refuses_before_get(tmp_path, monkeypatch, before, after):
    session, account, _, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan(urls()[before])]
    try:
        offer = session.rights_observation_snapshot(account).rights[0].delivery_offer_id
        original = client.loans
        digital_reads = 0
        def changed(upstream, borrower):
            nonlocal digital_reads
            if upstream == "DILICOM":
                digital_reads += 1
                if digital_reads == 2:
                    client.digital = [raw_loan(urls()[after])]
            return original(upstream, borrower)
        client.loans = changed
        with pytest.raises(ProviderCapabilityError, match="paris_candidate_route_changed"):
            session.downloadable_selected_delivery_candidate_guarded(
                account, **args(offer), target_dir=tmp_path, explicit_user_action=True,
                admission_current=lambda: True,
            )
        assert digital_reads == 2 and gets == [] and not list(tmp_path.iterdir())
    finally:
        session.close()
