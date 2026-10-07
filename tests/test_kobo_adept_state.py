from __future__ import annotations

from pathlib import Path

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    return LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))


def _probe(*, status_error: dict | None = None, capabilities_ms: int = 7300) -> dict:
    return {
        "contract": {
            "version": "0.13.0",
            "contract_version": 8,
            "capabilities": ["download_http_epub_pdf", "strict_json_stdout"],
        },
        "capabilities": {
            "ok": True,
            "software_version": "0.13.0",
            "contract_version": 8,
            "capabilities": {
                "download": {
                    "http": {
                        "available": True,
                        "formats": ["epub", "pdf"],
                    }
                }
            },
        },
        "status_error": status_error,
        "resolved_executable": "/home/test/.local/bin/kobo-adept",
        "probe_timings_ms": {
            "contract": 100,
            "capabilities": capabilities_ms,
            "status": 150 if status_error else 0,
        },
    }


def test_optional_status_config_error_does_not_block_core_runtime_state(tmp_path: Path):
    service = _service(tmp_path)

    class ProbeAdept:
        required_contract = 8

        def available(self):
            return True

        def probe_core(self):
            return _probe(
                status_error={"code": "config_error", "message": "invalid local.toml"},
                capabilities_ms=7300,
            )

    service._kobo_adapter = lambda: ProbeAdept()
    try:
        state = service.kobo_adept_state(force=True)

        assert state["available"] is True
        assert state["compatible"] is True
        assert state["contract_version"] == 8
        assert state["download_available"] is True
        assert state["operational"] is True
        assert state["status_error"]["code"] == "config_error"
        assert state["error"] is None
        assert state["probe_timings_ms"]["capabilities"] == 7300
    finally:
        service.close()


def test_status_timeout_is_non_blocking_and_cached_for_ui_calls(tmp_path: Path):
    service = _service(tmp_path)

    class ProbeAdept:
        required_contract = 8

        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def probe_core(self):
            self.calls += 1
            return _probe(
                status_error={
                    "code": "timeout",
                    "message": "status timeout",
                    "retryable": True,
                },
                capabilities_ms=7400,
            )

    fake = ProbeAdept()
    service._kobo_adapter = lambda: fake
    try:
        first = service.kobo_adept_state(force=True)
        second = service.kobo_adept_state()

        assert first["compatible"] is True
        assert first["operational"] is True
        assert first["status_error"]["code"] == "timeout"
        assert first["error"] is None
        assert second["contract_version"] == 8
        assert second["probe_timings_ms"]["capabilities"] == 7400
        assert fake.calls == 1
    finally:
        service.close()
