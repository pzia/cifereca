"""#3252: installed Kobo-Adept public service is the only Kobo DRM dispatcher.

No real CLI, Vault network, or provider credentials. The source.type=kobo
recipe must never be downgraded to transport.http or routed to an absent ID.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService


def test_kobo_kdrm_dispatch_uses_installed_runtime_download_request():
    calls = []
    recipe = {
        "schema_version": 2, "source": {
            "type": "kobo", "provider": "kobo-vault",
            "request": {"url": "https://api.kobo.com/fictional"},
            "candidate": {"drm_type": "KDRM"},
        },
        "destination": "/tmp/fictional-private-attempt.epub", "format": "epub",
    }
    result = {"ok": True, "artifact": {"path": recipe["destination"]}}

    def service(plugin):
        calls.append(("service", plugin))
        if plugin != "runtime.kobo-adept":
            pytest.fail("KDRM must not resolve a made-up DRM plugin or HTTP")
        return NS(download_request=lambda request: (
            calls.append(("runtime", request)) or result
        ))

    dummy = NS(_plugin_manager=object(), _required_plugin_service=service)
    assert LibraryService.execute_acquisition_request(dummy, recipe) == result
    assert calls == [
        ("service", "runtime.kobo-adept"),
        ("runtime", recipe),
    ]


def test_kobo_runtime_unavailable_never_falls_back_to_http():
    recipe = {"source": {"type": "kobo", "provider": "kobo-vault"},
              "destination": "/tmp/no-file.epub", "format": "epub"}
    calls = []
    def service(plugin):
        calls.append(plugin)
        raise RuntimeError("runtime offline")
    dummy = NS(_plugin_manager=object(), _required_plugin_service=service)
    with pytest.raises(RuntimeError, match="runtime offline"):
        LibraryService.execute_acquisition_request(dummy, recipe)
    assert calls == ["runtime.kobo-adept"]
