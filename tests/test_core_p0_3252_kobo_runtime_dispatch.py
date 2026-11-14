"""#3252/#5975: runtime source recipes use reciprocal installed manifests.

No real CLI, Vault network, or provider credentials. The source.type=kobo
recipe never degrades to HTTP; runtime IDs are not caller-controlled.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService


def test_kobo_kdrm_dispatch_uses_attested_runtime_download_request():
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
    runtime = NS(download_request=lambda request: (
        calls.append(("runtime", request)) or result
    ))
    def service(plugin):
        calls.append(("service", plugin))
        assert plugin == "runtime.kobo-adept"
        return runtime

    def binding(provider):
        calls.append(("binding", provider))
        return "runtime.kobo-adept" if provider == "kobo" else None

    dummy = NS(
        _plugin_manager=NS(registry_generation=5),
        runtime_acquisition_binding=binding,
        _required_plugin_service=service,
    )
    assert LibraryService.execute_acquisition_request(dummy, recipe) == result
    assert calls == [
        ("binding", "kobo"),
        ("service", "runtime.kobo-adept"),
        ("binding", "kobo"),
        ("runtime", recipe),
        ("binding", "kobo"),
        ("service", "runtime.kobo-adept"),
    ]


def test_kobo_runtime_unavailable_never_falls_back_to_http():
    recipe = {"source": {"type": "kobo", "provider": "kobo-vault"},
              "destination": "/tmp/no-file.epub", "format": "epub"}
    calls = []
    def service(plugin):
        calls.append(plugin)
        raise RuntimeError("runtime offline")
    dummy = NS(
        _plugin_manager=NS(registry_generation=1),
        runtime_acquisition_binding=lambda provider: (
            "runtime.kobo-adept" if provider == "kobo" else None
        ),
        _required_plugin_service=service,
    )
    with pytest.raises(RuntimeError, match="runtime offline"):
        LibraryService.execute_acquisition_request(dummy, recipe)
    assert calls == ["runtime.kobo-adept"]
