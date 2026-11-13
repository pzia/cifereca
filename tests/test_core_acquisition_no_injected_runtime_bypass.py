"""#5048: acquisition recipes cannot bypass plugin routing via a test adapter."""

from types import SimpleNamespace

import pytest

from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def test_unconfigured_manager_never_invokes_injected_kobo_adapter():
    calls = []

    def forbidden_adapter():
        calls.append("adapter")
        raise AssertionError("legacy adapter dispatcher must be unreachable")

    def missing_plugin(plugin_id):
        calls.append(("plugin", plugin_id))
        raise ProviderConfigurationError("plugin_unavailable")

    facade = SimpleNamespace(
        _plugin_manager=None,
        _kobo_adapter=forbidden_adapter,
        _required_plugin_service=missing_plugin,
    )
    request = {
        "source": {"type": "kobo"},
        "destination": "/tmp/synthetic.epub",
    }

    with pytest.raises(ProviderConfigurationError, match="plugin_unavailable"):
        LibraryService.execute_acquisition_request(facade, request)

    assert calls == [("plugin", "runtime.kobo-adept")]


def test_invalid_recipe_cannot_invoke_an_injected_runtime():
    calls = []
    facade = SimpleNamespace(
        _plugin_manager=None,
        _kobo_adapter=lambda: calls.append("adapter"),
    )

    with pytest.raises(ProviderConfigurationError, match="sans source"):
        LibraryService.execute_acquisition_request(facade, {})

    assert calls == []


def test_kobo_recipe_still_routes_through_installed_runtime():
    calls = []
    expected = {"ok": True}
    request = {"source": {"type": "kobo"}}

    facade = SimpleNamespace(
        _plugin_manager=object(),
        _kobo_adapter=lambda: pytest.fail("test-only adapter bypass"),
        _required_plugin_service=lambda plugin_id: (
            calls.append(plugin_id)
            or SimpleNamespace(download_request=lambda value: (
                calls.append(value) or expected
            ))
        ),
    )

    assert LibraryService.execute_acquisition_request(facade, request) == expected
    assert calls == ["runtime.kobo-adept", request]
