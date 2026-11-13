"""#5851: reciprocal manifest ownership, not a runtime readiness guess."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core import provider_sessions
from cifereca.plugin_api.models import PluginDescriptor
from cifereca.plugins.manifest import ManifestError, parse_manifest


_ROOT = Path(__file__).resolve().parents[1] / "src/plugins"


def _manifest(name: str):
    return json.loads((_ROOT / name / "cifereca-plugin.json").read_text(encoding="utf-8"))


def test_kobo_provider_and_runtime_manifest_declare_reciprocal_binding():
    provider = parse_manifest(_manifest("provider-kobo")).plugin("provider.kobo")
    runtime = parse_manifest(_manifest("runtime-kobo-adept")).plugin("runtime.kobo-adept")
    assert provider.runtime_acquisition_service == "runtime.kobo-adept"
    assert runtime.runtime_acquisition_providers == ("kobo",)
    assert provider.type == "provider.library"
    assert runtime.type == "runtime.adapter"
    # Neither declaration is a new hard dependency or permission grant.
    assert not any(dep.plugin == "runtime.kobo-adept" for dep in provider.requires)


def test_manifest_rejects_malformed_or_misplaced_runtime_binding():
    base = _manifest("provider-kobo")
    for invalid in ("", "NOT AN ID", [], None, 17, "runtime.\x00forged"):
        data = json.loads(json.dumps(base))
        data["plugins"][1]["runtime_acquisition_service"] = invalid
        with pytest.raises(ManifestError):
            parse_manifest(data)
    data = json.loads(json.dumps(base))
    data["plugins"][0]["runtime_acquisition_service"] = "runtime.kobo-adept"
    with pytest.raises(ManifestError):
        parse_manifest(data)

    base = _manifest("runtime-kobo-adept")
    for invalid in ([], "kobo", [None], ["BAD ID"], [17]):
        data = json.loads(json.dumps(base))
        data["plugins"][0]["runtime_acquisition_providers"] = invalid
        with pytest.raises(ManifestError):
            parse_manifest(data)
    data = json.loads(json.dumps(base))
    data["plugins"][1]["runtime_acquisition_providers"] = ["kobo"]
    with pytest.raises(ManifestError):
        parse_manifest(data)


def _descriptor(plugin_id: str, category: str, **kwargs):
    return PluginDescriptor(
        id=plugin_id, type=category, version="1.0.0",
        package_id="test.package", package_version="1.0.0", **kwargs,
    )


def _status(descriptor, *, enabled=True, state="ready"):
    return SimpleNamespace(descriptor=descriptor, enabled=enabled, state=state)


class _Manager:
    def __init__(self, statuses):
        self.statuses_by_id = statuses
        self.status_calls = []
        self.service_calls = []

    def status(self, plugin_id):
        self.status_calls.append(plugin_id)
        return self.statuses_by_id[plugin_id]

    def service(self, plugin_id):
        self.service_calls.append(plugin_id)
        raise AssertionError("binding read must never activate a runtime")


def _core(provider, runtime, *, provider_id="kobo"):
    manager = _Manager({"provider.kobo": _status(provider),
                        "runtime.kobo-adept": _status(runtime)})
    registry = SimpleNamespace(maybe_get=lambda key: (
        SimpleNamespace(descriptor=SimpleNamespace(id="kobo"))
        if key == "kobo" else None
    ))
    return SimpleNamespace(
        _plugin_manager=manager, registry=registry,
        _provider_plugin_ids={"kobo": "provider.kobo"},
    ), manager


def test_only_exact_reciprocal_ready_registry_source_is_attested():
    provider = _descriptor(
        "provider.kobo", "provider.library",
        runtime_acquisition_service="runtime.kobo-adept",
    )
    runtime = _descriptor(
        "runtime.kobo-adept", "runtime.adapter",
        runtime_acquisition_providers=("kobo",),
    )
    core, manager = _core(provider, runtime)
    assert provider_sessions.runtime_acquisition_binding(core, "kobo") == "runtime.kobo-adept"
    assert manager.status_calls == ["provider.kobo", "runtime.kobo-adept"]
    assert manager.service_calls == []
    assert provider_sessions.runtime_acquisition_binding(core, "synthetic") is None
    assert manager.service_calls == []


def test_missing_foreign_disabled_or_forged_registry_claims_fail_closed():
    provider = _descriptor(
        "provider.kobo", "provider.library",
        runtime_acquisition_service="runtime.kobo-adept",
    )
    runtime = _descriptor(
        "runtime.kobo-adept", "runtime.adapter",
        runtime_acquisition_providers=("kobo",),
    )
    for alternate_provider, alternate_runtime in (
        (replace(provider, runtime_acquisition_service=None), runtime),
        (provider, replace(runtime, runtime_acquisition_providers=("other",))),
        (provider, replace(runtime, type="provider.library")),
        (provider, replace(runtime, id="runtime.other")),
        (replace(provider, id="provider.forged"), runtime),
    ):
        core, manager = _core(alternate_provider, alternate_runtime)
        assert provider_sessions.runtime_acquisition_binding(core, "kobo") is None
        assert manager.service_calls == []

    core, manager = _core(provider, runtime)
    manager.statuses_by_id["runtime.kobo-adept"] = _status(runtime, enabled=False)
    assert provider_sessions.runtime_acquisition_binding(core, "kobo") is None
    manager.statuses_by_id["runtime.kobo-adept"] = _status(runtime, state="failed")
    assert provider_sessions.runtime_acquisition_binding(core, "kobo") is None
    manager.statuses_by_id["runtime.kobo-adept"] = _status(runtime)
    manager.statuses_by_id["provider.kobo"] = _status(provider, enabled=False)
    assert provider_sessions.runtime_acquisition_binding(core, "kobo") is None
    manager.statuses_by_id.pop("provider.kobo")
    assert provider_sessions.runtime_acquisition_binding(core, "kobo") is None
    assert manager.service_calls == []
