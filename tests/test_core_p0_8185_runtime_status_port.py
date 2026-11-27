"""Native status cutover: descriptive only, no fallback on malformed ports."""
from types import SimpleNamespace
import pytest

from cifereca.plugin_api import RuntimeStatusSnapshot
from tests.test_kobo_adept_state import _service, _set_adept_runtime, _kdrm_probe


def _native(**changes):
    fields = dict(
        available=True, compatible=True, operational=True, download_available=True,
        acsm_available=True, delegated_available=True, pinned_delivery_candidate=True,
        separate_content_keys_replay=True, protected_content_available=True,
        formats=("epub",), contract_version=8, minimum_supported_contract=8,
        details={"capabilities": {}, "strict_json_stdout": True},
    )
    fields.update(changes)
    return RuntimeStatusSnapshot(**fields)


def test_native_port_is_preferred_without_raw_probe(tmp_path):
    service = _service(tmp_path)
    calls = []

    def native(*, full=False):
        calls.append(full)
        return _native()

    def forbidden():
        pytest.fail("native port must never call the old wire-schema reader")

    adapter = SimpleNamespace(runtime_status=native, probe_core=forbidden)
    _set_adept_runtime(service, lambda: adapter)
    try:
        state = service.kobo_adept_state(force=True)
        assert state["operational"] is True
        assert state["kobo_kdrm_available"] is True
        assert state["kobo_formats"] == ["epub"]
        assert service.kobo_adept_state() == state
        service.kobo_adept_state(full=True)
        assert calls == [False, True]
    finally:
        service.close()


@pytest.mark.parametrize("bad", [
    None,
    {"available": True},
    _native(schema="foreign/2"),
    _native(available="yes"),
    _native(available=False),
    _native(delegated_available=False),
    _native(details={"operational": True}),
    _native(details={"strict_json_stdout": "yes"}),
])
def test_bad_native_result_is_not_downgraded_or_cached(tmp_path, bad):
    service = _service(tmp_path)
    calls = []

    def native(*, full=False):
        calls.append(full)
        return bad if len(calls) == 1 else _native()

    def forbidden():
        pytest.fail("invalid native result must not fall back to raw probe")

    _set_adept_runtime(service, lambda: SimpleNamespace(
        runtime_status=native, probe_core=forbidden,
    ))
    try:
        state = service.kobo_adept_state()
        assert state["available"] is False
        assert state["operational"] is False
        assert state["error"]["code"] == "runtime_status_contract_invalid"
        assert service.kobo_adept_state()["operational"] is True
        assert calls == [False, False]
    finally:
        service.close()


def test_old_installed_runtime_retains_read_only_status(tmp_path):
    service = _service(tmp_path)
    calls = []
    adapter = SimpleNamespace(
        available=lambda: True, required_contract=8,
        probe_core=lambda: calls.append("legacy") or _kdrm_probe(),
    )
    _set_adept_runtime(service, lambda: adapter)
    try:
        assert service.kobo_adept_state()["kobo_kdrm_available"] is True
        assert calls == ["legacy"]
    finally:
        service.close()


def test_forced_invalid_status_revokes_previous_cached_positive(tmp_path):
    service = _service(tmp_path)
    calls = []

    def native(*, full=False):
        calls.append(full)
        return _native() if len(calls) == 1 else None

    _set_adept_runtime(service, lambda: SimpleNamespace(runtime_status=native))
    try:
        assert service.kobo_adept_state()["operational"] is True
        assert service.kobo_adept_state(force=True)["operational"] is False
        assert service.kobo_adept_state()["operational"] is False
        assert calls == [False, False, False]
    finally:
        service.close()
