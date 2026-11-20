"""#7161: real runtime preflight is a fresh, read-only capability probe.

No Adobe subprocess, signed URL, token, file or production loan is used.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca_kobo_adept_runtime import KoboAdeptRuntime
from cifereca.plugin_api import UserFacingError


def caps(*, names, available=True):
    return {"capabilities": {
        "download": {"acsm": {"available": available}},
        "adept": {"fetch_capable_profiles": names},
    }}


def rig(refreshed, destination):
    runtime = KoboAdeptRuntime(NS(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *_a, **_kw: None,
    ))
    runtime.contract_version = 8
    runtime._capabilities_cache = caps(names=["stale-reader"])
    calls = []

    def fake_json(argv, **kwargs):
        calls.append(list(argv))
        if argv == ["capabilities", "--json"]:
            return refreshed
        if argv == ["download", "request", "-", "--json"]:
            assert kwargs["payload"]["profile"] == "reader-a"
            return {"ok": True, "artifact": {"path": str(destination)}}
        pytest.fail("unexpected/possibly mutating command")
    runtime.run_json = fake_json
    return runtime, calls


def request(destination, profile="reader-a"):
    return {
        "schema_version": 2,
        "source": {"type": "acsm", "path": "/synthetic/token.acsm"},
        "destination": str(destination),
        "format": "epub", "profile": profile,
    }


def test_fresh_capabilities_replace_cached_negative_before_download(tmp_path):
    target = tmp_path / "chosen.epub"
    runtime, calls = rig(caps(names=["reader-a"]), target)
    assert runtime.download_request(request(target))["ok"] is True
    assert calls == [["capabilities", "--json"],
                     ["download", "request", "-", "--json"]]


@pytest.mark.parametrize("refreshed,profile,code", [
    (caps(names=[]), "reader-a", "adept_profile_required"),
    (caps(names=["other-reader"]), "reader-a", "adept_profile_required"),
    (caps(names=["reader-a"], available=False), "reader-a", "unsupported_format"),
    (caps(names="reader-a"), "reader-a", "kobo_adept_invalid_response"),
    (caps(names=["reader-a", "reader-a"]), "reader-a",
     "kobo_adept_invalid_response"),
    (caps(names=["reader-a"]), "other-reader", "adept_profile_required"),
    (caps(names=["reader-a"]), "", "adept_profile_required"),
])
def test_negative_preflight_never_reaches_download_request(tmp_path, refreshed, profile, code):
    target = tmp_path / "chosen.epub"
    runtime, calls = rig(refreshed, target)
    with pytest.raises(UserFacingError) as err:
        runtime.download_request(request(target, profile))
    assert err.value.code == code
    assert calls == [["capabilities", "--json"]] if profile else calls == []
    assert not target.exists()


def test_explicit_preflight_alone_is_read_only(tmp_path):
    runtime, calls = rig(caps(names=["reader-a"]), tmp_path / "unused.epub")
    assert runtime.preflight_acsm_profile("reader-a") is True
    assert calls == [["capabilities", "--json"]]
