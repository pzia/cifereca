"""Synthetic signed ACSM deliveries only; no private token or live download."""
import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
for name in ("biblioaccess_observations", "biblioaccess_acsm_delivery"):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)

delivery = sys.modules["biblioaccess_acsm_delivery"]
URL = "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm?code=PRIVATE-SYNTHETIC-CODE"
ACSM = (
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
    b'fulfillmentType="loan"><permissions><display><duration>3600</duration>'
    b'</display></permissions></fulfillmentToken>'
)


def test_direct_signed_acsm_get_stages_fresh_file_without_url_in_output(tmp_path):
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path,
                     request.url.params["code"], request.headers.get("cookie")))
        return httpx.Response(200, headers={"content-type":"application/vnd.adobe.adept+xml"},
                              content=ACSM)
    for _ in range(2):
        staged = delivery.stage_loan_acsm(
            URL, tmp_path, transport=httpx.MockTransport(handler))
        assert staged.suffix == ".acsm"
        assert staged.read_bytes() == ACSM
        assert "PRIVATE" not in str(staged)
    assert seen == [
        ("GET", "/fulfillment/URLLink.acsm", "PRIVATE-SYNTHETIC-CODE", None),
        ("GET", "/fulfillment/URLLink.acsm", "PRIVATE-SYNTHETIC-CODE", None),
    ]
    assert len(tuple(tmp_path.glob("*.acsm"))) == 2  # Re-download allowed.


@pytest.mark.parametrize("url", [
    "http://entrepot2r.numilog.com/fulfillment/URLLink.acsm?code=private",
    "https://evil.example/fulfillment/URLLink.acsm?code=private",
    "https://entrepot2r.numilog.com.evil.example/fulfillment/URLLink.acsm?code=private",
    "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm",
    "https://entrepot2r.numilog.com/other?code=private",
    "https://attacker:password@entrepot2r.numilog.com/fulfillment/URLLink.acsm?code=private",
])
def test_untrusted_acsm_origins_never_reach_transport(url, tmp_path):
    with pytest.raises(delivery.ACSMDeliveryError, match="url_invalid"):
        delivery.stage_loan_acsm(url, tmp_path,
                                 transport=httpx.MockTransport(
                                     lambda _: pytest.fail("unexpected GET")))


def test_failed_or_non_acsm_response_never_writes_file(tmp_path):
    for status, body in [(403, ACSM), (302, ACSM), (200, b"not an acsm")]:
        with pytest.raises(delivery.ACSMDeliveryError):
            delivery.stage_loan_acsm(
                URL, tmp_path, transport=httpx.MockTransport(
                    lambda _, st=status, content=body: httpx.Response(
                        st, content=content)))
        assert list(tmp_path.iterdir()) == []


def test_oversized_xml_rejected_before_creating_artifact(tmp_path):
    with pytest.raises(delivery.ACSMDeliveryError, match="body_limit"):
        delivery.stage_loan_acsm(
            URL, tmp_path, transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"x" * 128001)))
    assert list(tmp_path.iterdir()) == []


PNB_URL = "https://pnb-dilicom.centprod.com/fulfillment/synthetic/do"


def test_pnb_do_stages_valid_acsm_without_extension(tmp_path):
    requests = []
    def handler(request):
        requests.append((request.method, request.url.host, request.url.path))
        return httpx.Response(200, content=ACSM,
                              headers={"content-type": "application/octet-stream"})
    result = delivery.stage_loan_acsm(
        PNB_URL, tmp_path, transport=httpx.MockTransport(handler))
    assert result.read_bytes() == ACSM
    assert requests == [("GET", "pnb-dilicom.centprod.com",
                         "/fulfillment/synthetic/do")]


def test_pnb_same_origin_redirect_stages_acsm(tmp_path):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/do"):
            return httpx.Response(302, headers={"location": "/signed/short-lived"})
        return httpx.Response(200, content=ACSM)
    result = delivery.stage_loan_acsm(
        PNB_URL, tmp_path, transport=httpx.MockTransport(handler))
    assert result.read_bytes() == ACSM
    assert paths == ["/fulfillment/synthetic/do", "/signed/short-lived"]


@pytest.mark.parametrize("status,body", [
    (403, ACSM), (404, ACSM), (200, b"<html>login</html>"),
    (200, b"invalid payload"),
])
def test_pnb_failed_or_non_acsm_never_stages(status, body, tmp_path):
    with pytest.raises(delivery.ACSMDeliveryError):
        delivery.stage_loan_acsm(
            PNB_URL, tmp_path,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, content=body)))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("location", [
    "https://evil.example/steal", "//evil.example/steal",
    "http://pnb-dilicom.centprod.com/insecure",
    "https://username:password@pnb-dilicom.centprod.com/secret",
    "https://pnb-dilicom.centprod.com:444/odd",
])
def test_pnb_cross_origin_or_unsafe_redirect_never_followed(location, tmp_path):
    observed = []
    def handler(request):
        observed.append(request.url.path)
        return httpx.Response(302, headers={"location": location})
    with pytest.raises(delivery.ACSMDeliveryError, match="acsm_redirect"):
        delivery.stage_loan_acsm(
            PNB_URL, tmp_path, transport=httpx.MockTransport(handler))
    assert observed == ["/fulfillment/synthetic/do"]
    assert list(tmp_path.iterdir()) == []


def test_pnb_redirect_loop_is_bounded(tmp_path):
    observed = []
    def handler(request):
        observed.append(request.url.path)
        return httpx.Response(302, headers={"location": "/again"})
    with pytest.raises(delivery.ACSMDeliveryError, match="acsm_redirect"):
        delivery.stage_loan_acsm(
            PNB_URL, tmp_path, transport=httpx.MockTransport(handler))
    assert len(observed) == 3
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("url", [
    "https://pnb-dilicom.centprod.com.evil.example/a/do",
    "http://pnb-dilicom.centprod.com/a/do",
    "https://pnb-dilicom.centprod.com/a/do#fragment",
    "https://pnb-dilicom.centprod.com/a/not-do",
    "https://pnb-dilicom.centprod.com/a/../do",
])
def test_pnb_untrusted_actions_never_reach_transport(url, tmp_path):
    with pytest.raises(delivery.ACSMDeliveryError, match="url_invalid"):
        delivery.stage_loan_acsm(
            url, tmp_path, transport=httpx.MockTransport(
                lambda _: pytest.fail("unexpected GET")))
