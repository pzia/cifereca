"""P1 #2845 — probe timing evidence without CLI, profile or user data."""
from types import MethodType, SimpleNamespace
import pytest
import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptRuntime


def _runtime():
    return KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept", "--opaque=PRIVATE"]},
        report_diagnostic=lambda *args, **kwargs: None, logger=None,
    ))


def test_exact_core_probe_calls_have_independent_bounded_public_times(monkeypatch):
    r = _runtime()
    calls = []
    r.contract = MethodType(lambda _self: calls.append("contract") or {
        "name": "kobo-adept", "contract_version": 8,
        "token": "PRIVATE_PROFILE", "version": "0.13.6",
    }, r)
    r.capabilities = MethodType(lambda _self: calls.append("capabilities") or {
        "ok": True, "software_version": "0.13.6",
        "secret": "PRIVATE_PROFILE", "capabilities": {},
    }, r)
    ticks = iter([1000000000, 1017000000, 1020000000, 1029000000])
    monkeypatch.setattr(module.time, "perf_counter_ns", lambda: next(ticks))
    monkeypatch.setattr(module.shutil, "which", lambda _: "/home/PRIVATE/bin/kobo-adept")
    result = r.probe_core()
    assert calls == ["contract", "capabilities"]
    assert result["probe_timings_ms"] == {"contract": 17, "capabilities": 9}
    assert result["resolved_executable"] is True
    assert "PRIVATE" not in repr(result)


def test_failed_contract_never_calls_capabilities_or_records_probe_success(monkeypatch):
    r = _runtime()
    def failing(_self):
        raise RuntimeError("PRIVATE_PROFILE")
    r.contract = MethodType(failing, r)
    r.capabilities = MethodType(
        lambda _self: pytest.fail("capabilities called after contract failure"), r)
    monkeypatch.setattr(module.time, "perf_counter_ns", lambda: 1000000000)
    with pytest.raises(RuntimeError, match="PRIVATE_PROFILE"):
        r.probe_core()


def test_probe_time_limits_include_negative_clock_anomaly(monkeypatch):
    r = _runtime()
    r.contract = MethodType(lambda _self: {
        "name": "kobo-adept", "contract_version": 8}, r)
    r.capabilities = MethodType(lambda _self: {
        "ok": True, "capabilities": {}}, r)
    ticks = iter([10000000000, 1000000000, 1000000000, 10000000000000000])
    monkeypatch.setattr(module.time, "perf_counter_ns", lambda: next(ticks))
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert r.probe_core()["probe_timings_ms"] == {
        "contract": 0, "capabilities": 600000}
