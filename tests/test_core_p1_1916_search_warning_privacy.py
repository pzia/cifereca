"""#1916: no provider/callback free-form warning may reach Core diagnostics."""
from __future__ import annotations

from types import SimpleNamespace as NS

from tests.test_core043_progress_cache_stats import _service, _FakeSession


SENTINEL = "SIGNED-TOKEN-PRIVATE-PATH-DO-NOT-EXPOSE"


def test_sync_provider_and_client_warning_is_redacted_preserving_live_result(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        provider = _FakeSession("catalog")
        provider.last_search_warnings = [
            "https://private.invalid/fulfil?token=" + SENTINEL,
        ]
        provider.client = NS(last_search_warnings=[
            {"secret": SENTINEL},  # warnings need not even be strings
        ])
        service._sessions = {"catalog": provider}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kw: len(rows))
        rows = service.search("Progressive Book", field="title", force=True)
        assert len(rows) == 1
        assert rows[0].title == "Progressive Book"
        assert service.last_search_warnings == ["Catalog: résultats partiels"]
        assert SENTINEL not in repr(service.last_search_warnings)
    finally:
        service.close()


def test_progressive_callback_exception_never_records_raw_details(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        service._sessions = {"catalog": _FakeSession("catalog")}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kw: len(rows))
        records = []
        original = service.diagnostics.add

        def capture(*args, **kwargs):
            records.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(service.diagnostics, "add", capture)

        class HostileCallbackError(RuntimeError):
            def __str__(self):
                return "signed URL " + SENTINEL

            def __repr__(self):
                raise AssertionError("Unexpected exception repr")

        def break_callback(_batch):
            raise HostileCallbackError("sensitive")

        summary = service.search_progressive(
            "Progressive Book", force=True, on_batch=break_callback,
        )
        assert summary.result_count == 1
        assert summary.providers_failed == 0
        callback_errors = [
            kwargs["detail"] for args, kwargs in records
            if args[:2] == ("SEARCH", "progress callback")
        ]
        assert callback_errors == [{
            "error": "CallbackFailed",
            "message": "search_progress_callback_failed",
        }]
        assert SENTINEL not in repr(callback_errors)
    finally:
        service.close()


def test_worker_provider_error_code_is_bounded_even_for_hostile_exception_class(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        source = _FakeSession("bad")
        class LEAK_SENSITIVE_EXCEPTION_NAME(BaseException):
            pass
        # A RuntimeError subclass with arbitrarily chosen (potentially
        # private) class names must not become a provider error code.
        RogueError = type("SIGNED_TOKEN_SHOULD_NOT_BE_A_CODE", (RuntimeError,), {})
        def bad_search(*args, **kwargs):
            raise RogueError(SENTINEL)
        source.search = bad_search
        service._sessions = {"bad": source}
        rows, warnings, code, message = service._search_provider_session(
            source, "Progressive Book", "all", field="all",
        )
        assert rows == []
        assert code == "ProviderSearchFailed"
        assert SENTINEL not in repr((warnings, code, message))
    finally:
        service.close()
