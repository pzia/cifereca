"""P1 #4048: retired voluntary DRM return never reaches a legacy adapter.

Synthetic in-memory plugin; no ADEPT binary, credentials, I/O or network.
Other supported DRM mutations remain routed unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.services import AttentionRequired, LibraryService


class NoServiceAccess:
    """Any call to a plugin manager, storage, diagnostics or clock is a bug."""

    def __getattribute__(self, name):
        raise AssertionError("Core return refusal accessed service: " + name)


@pytest.mark.parametrize("operation", ["return", "RETURN", "ReTuRn"])
def test_retired_drm_return_denied_before_plugin_discovery(operation):
    request = NS(operation=operation)
    with pytest.raises(AttentionRequired) as exc:
        LibraryService.execute_drm_mutation(NoServiceAccess(), request)
    assert exc.value.code == "loan_return_removed"
    assert "Restitution volontaire retirée" in str(exc.value)


def test_non_return_drm_mutation_keeps_existing_runtime_dispatch():
    calls = []
    adapter = NS(
        supports=lambda request: calls.append(("supports", request.operation)) or True,
        execute=lambda request: calls.append(("execute", request.operation))
        or {"ok": True, "operation": request.operation},
    )
    manager = NS(
        candidates=lambda capability: (
            calls.append(("candidates", capability))
            or [NS(descriptor=NS(id="runtime.synthetic"))]
        ),
        service=lambda plugin_id: (
            calls.append(("service", plugin_id)) or adapter
        ),
    )
    host = NS(_plugin_manager=manager)
    result = LibraryService.execute_drm_mutation(host, NS(operation="renew"))
    assert result == {"ok": True, "operation": "renew"}
    assert calls == [
        ("candidates", "runtime.adapter"),
        ("service", "runtime.synthetic"),
        ("supports", "renew"),
        ("execute", "renew"),
    ]
