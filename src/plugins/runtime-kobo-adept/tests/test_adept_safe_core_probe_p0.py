"""P0 #979: Core/Diagnostics must see typed flags, never vendor probe extras.

Synthetic external-success contracts only, no subprocess or actual profiles.
"""
from types import MethodType, SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptRuntime
from cifereca_runtime_probe_safe import public_core_probe

SECRET = "PRIVATE_SESSION"
URL = "https://licenses.invalid/file?token=PRIVATE_SESSION"
HOME = "/home/secret-person/.local/bin/kobo-adept"
XML = "<fulfillmentToken>PRIVATE_SESSION</fulfillmentToken>"


def source():
    contract = {
        "ok": True, "name": "kobo-adept",
        "contract_version": 8, "version": "0.13.6",
        "capabilities": [
            "strict_json_stdout", "download_http_epub_pdf",
            "download_acsm_adept", "download_kobo_delegated",
            SECRET, URL, {"private": XML},
        ],
        "license": XML, "account": "alice@example.invalid",
    }
    capabilities = {
        "ok": True, "contract_version": 8, "software_version": "0.13.6",
        "account": "alice@example.invalid", "key": SECRET,
        "capabilities": {
            "adept": {
                "loan_return": {"available": True, "token": SECRET},
                "fetch_capable_profiles": ["alice@example.invalid"],
                "certificate": XML,
            },
            "download": {
                "http": {"available": True, "cookies": SECRET},
                "acsm": {"available": True, "last_license": XML},
                "kobo": {
                    "available": True,
                    "formats": ["epub", "pdf", "kepub", SECRET, URL],
                    "pinned_delivery_candidate": True,
                    "separate_content_keys_replay": True,
                    "drm": {
                        "KDRM": {"available": True, "content_keys": SECRET}
                    },
                    "signed_url": URL,
                },
            },
            "private": {"user": "alice@example.invalid", "raw_xml": XML},
        },
    }
    return contract, capabilities


def assert_private_absent(result):
    raw = repr(result)
    for hidden in (SECRET, URL, XML, HOME, "alice@example.invalid",
                   "certificate", "'content_keys':", "signed_url"):
        assert hidden not in raw


def test_strict_core_probe_preserves_only_core_capabilities():
    contract, caps = source()
    output = public_core_probe(contract, caps, True)
    assert output["contract"] == {
        "name": "kobo-adept", "contract_version": 8,
        "version": "0.13.6",
        "capabilities": [
            "download_acsm_adept", "download_http_epub_pdf",
            "download_kobo_delegated", "strict_json_stdout",
        ],
    }
    assert output["capabilities"]["software_version"] == "0.13.6"
    flags = output["capabilities"]["capabilities"]["download"]
    assert flags["acsm"]["available"] is True
    assert flags["http"]["available"] is True
    assert flags["kobo"]["available"] is True
    assert flags["kobo"]["formats"] == ["epub", "kepub", "pdf"]
    assert flags["kobo"]["pinned_delivery_candidate"] is True
    assert flags["kobo"]["separate_content_keys_replay"] is True
    assert flags["kobo"]["drm"]["KDRM"]["available"] is True
    assert output["capabilities"]["capabilities"]["adept"]["loan_return"]["available"] is True
    assert output["resolved_executable"] is True
    assert_private_absent(output)


def test_real_runtime_probe_never_returns_argv_or_resolved_path(monkeypatch):
    contract, caps = source()
    calls = []
    adapter = KoboAdeptRuntime(SimpleNamespace(
        config={"command": [HOME, "--license=" + SECRET]},
        report_diagnostic=lambda *a, **k: None,
        logger=None,
    ))
    adapter.contract = MethodType(lambda _self: calls.append("contract") or contract, adapter)
    adapter.capabilities = MethodType(lambda _self: calls.append("capabilities") or caps, adapter)
    monkeypatch.setattr(module.shutil, "which", lambda path: HOME)
    output = adapter.probe_core()
    assert calls == ["contract", "capabilities"]
    assert output["resolved_executable"] is True
    assert_private_absent(output)
    assert adapter.probe() == output


@pytest.mark.parametrize(("contract","caps"), [
    ({"contract_version": SECRET, "capabilities": [URL]}, {"capabilities": {"download": {"kobo": {"available": SECRET}}}}),
    ({"contract_version": True, "version": URL}, {"software_version": XML, "capabilities": ["key"]}),
    ({"contract_version": -1}, {"capabilities": {"download": ["private"]}}),
    ({"contract_version": 10**100}, {"capabilities": {"adept": {"loan_return": "true"}}}),
    ({"capabilities": [{"certificate": SECRET}]}, {"capabilities": {}}),
    ({}, {}),
])
def test_unknown_types_and_vendor_extras_cannot_claim_capabilities(contract, caps):
    out = public_core_probe(contract, caps, False)
    assert out["contract"]["contract_version"] == 0
    assert out["resolved_executable"] is False
    assert out["capabilities"]["capabilities"]["download"]["http"]["available"] is False
    assert out["capabilities"]["capabilities"]["download"]["acsm"]["available"] is False
    assert_private_absent(out)


def test_vendor_truthiness_does_not_prove_download_feature():
    out = public_core_probe(
        {"contract_version": 8},
        {"capabilities": {"download": {
            "http": {"available": 1}, "acsm": {"available": "true"},
            "kobo": {
                "available": {"value": True},
                "pinned_delivery_candidate": "yes",
                "separate_content_keys_replay": 1,
                "formats": ["PRIVATE_SESSION"],
                "drm": {"KDRM": {"available": 1}},
            },
        }}},
        True,
    )
    d = out["capabilities"]["capabilities"]["download"]
    assert d["http"]["available"] is False
    assert d["acsm"]["available"] is False
    assert d["kobo"]["available"] is False
    assert d["kobo"]["pinned_delivery_candidate"] is False
    assert d["kobo"]["separate_content_keys_replay"] is False
    assert d["kobo"]["drm"]["KDRM"]["available"] is False
    assert not d["kobo"]["formats"]
    assert_private_absent(out)


def test_adaptive_capabilities_can_still_prove_acsm_from_contract_only():
    # Compatibility with Core's fallback: declared machine capabilities
    # remain evidence even when the nested capability map is absent.
    out = public_core_probe({
        "contract_version": 8,
        "capabilities": ["download_acsm_adept", "strict_json_stdout"],
    }, {}, True)
    assert "download_acsm_adept" in out["contract"]["capabilities"]
    assert out["capabilities"]["capabilities"]["download"]["acsm"]["available"] is False
    assert_private_absent(out)
