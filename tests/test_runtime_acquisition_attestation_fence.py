"""Fail closed when a runtime's ownership changes during its capability probe."""

from types import SimpleNamespace

from cifereca.services import LibraryService


class _Runtime:
    def __init__(self, on_probe=None):
        self.on_probe = on_probe

    def acquisition_capabilities(self, *, force=False):
        if self.on_probe:
            self.on_probe()
        return {
            "available": True, "compatible": True, "operational": True,
            "capabilities": {"download": {"http": {"available": True}}},
        }


def _service(runtime, claims):
    return SimpleNamespace(
        runtime_acquisition_binding=lambda provider: claims["runtime"],
        _plugin_manager=SimpleNamespace(service=lambda runtime_id: runtime),
    )


def test_runtime_capabilities_are_not_attested_after_rebinding():
    claims = {"runtime": "runtime.synthetic"}
    runtime = _Runtime(on_probe=lambda: claims.update(runtime=None))
    service = _service(runtime, claims)

    assert LibraryService.runtime_acquisition_state(
        service, "synthetic"
    ) == {}


def test_runtime_capabilities_are_attested_with_stable_reciprocal_binding():
    claims = {"runtime": "runtime.synthetic"}
    service = _service(_Runtime(), claims)

    state = LibraryService.runtime_acquisition_state(service, "synthetic")

    assert state["provider_attested"] is True
    assert state["provider_id"] == "synthetic"
    assert state["available"] is True


def test_runtime_producer_failure_never_attests():
    claims = {"runtime": "runtime.synthetic"}

    def fail():
        raise RuntimeError("private plugin exception")

    service = _service(_Runtime(on_probe=fail), claims)

    assert LibraryService.runtime_acquisition_state(service, "synthetic") == {}
