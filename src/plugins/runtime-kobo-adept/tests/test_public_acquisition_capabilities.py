"""Runtime v8 — safe descriptive acquisition facts, no subprocess or rights."""
from __future__ import annotations

from cifereca_kobo_adept_runtime import KoboAdeptRuntime


def _runtime(*, contract=8, declared=(), download=None, executable=True):
    runtime = KoboAdeptRuntime.__new__(KoboAdeptRuntime)
    runtime.calls = 0
    def probe_core():
        runtime.calls += 1
        return {
            "resolved_executable": executable,
            "contract": {"contract_version": contract,
                         "capabilities": list(declared)},
            "capabilities": {
                "software_version": "0.13.4",
                "capabilities": {"download": download or {}},
            },
        }
    runtime.probe_core = probe_core
    return runtime


def test_modes_follow_only_sanitized_runtime_flags():
    runtime = _runtime(
        declared=("download_http_epub_pdf",),
        download={
            "http": {"available": False},
            "acsm": {"available": True},
            "kobo": {"available": True, "formats": ["EPUB", "KEPUB"],
                     "drm": {"KDRM": {"available": True}}},
        },
    )
    result = runtime.acquisition_capabilities()
    assert result["modes"] == [
        "http_plain", "adobe_adept", "kobo_delegated", "kobo_kdrm",
    ]
    assert result["formats"] == ["EPUB", "KEPUB"]
    assert result["available"] is True
    assert result["operational"] is True
    assert "resolved_executable" not in result
    assert runtime.acquisition_capabilities()["modes"] == result["modes"]
    assert runtime.calls == 1
    runtime.acquisition_capabilities(force=True)
    assert runtime.calls == 2


def test_contract_version_alone_never_invents_modes():
    runtime = _runtime(contract=8)
    result = runtime.acquisition_capabilities()
    assert result["compatible"] is True
    assert result["modes"] == []
    assert result["operational"] is False


def test_old_contract_does_not_enable_delegated_mode():
    runtime = _runtime(contract=7, declared=("download_kobo_delegated",),
                       download={"kobo": {"available": True}})
    result = runtime.acquisition_capabilities()
    assert result["compatible"] is False
    assert "kobo_delegated" not in result["modes"]


def test_explicit_negative_kdrm_overrides_pinned_replay_flags():
    runtime = _runtime(download={
        "kobo": {"available": True,
                 "pinned_delivery_candidate": True,
                 "separate_content_keys_replay": True,
                 "drm": {"KDRM": {"available": False}}},
    })
    assert runtime.acquisition_capabilities()["modes"] == ["kobo_delegated"]


def test_missing_executable_never_cached():
    runtime = _runtime(executable=False, download={
        "acsm": {"available": True},
    })
    assert runtime.acquisition_capabilities()["operational"] is False
    assert runtime.acquisition_capabilities()["available"] is False
    assert runtime.calls == 2
