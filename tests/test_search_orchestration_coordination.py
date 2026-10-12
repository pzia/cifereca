"""Extraction keeps caller callbacks and the facade-owned search executor."""
from threading import get_ident

from cifereca import services
from tests.test_core_0968_provider_stream import Collection, _library, event


def test_progressive_search_uses_facade_executor_and_delivers_batches_on_caller(tmp_path, monkeypatch):
    executor = services._SEARCH_EXECUTOR
    submissions = []

    class ObservedExecutor:
        def submit(self, function, *args, **kwargs):
            submissions.append(function)
            return executor.submit(function, *args, **kwargs)

    monkeypatch.setattr(services, '_SEARCH_EXECUTOR', ObservedExecutor())
    service = _library(tmp_path, Collection([event('upsert', 1), event('done', 2)], stream_capability=False))
    caller = get_ident()
    callbacks = []
    try:
        summary = service.search_progressive(
            'book', force=True, on_batch=lambda batch: callbacks.append((get_ident(), batch)),
        )
        assert summary.result_count == 1 and not summary.cancelled
        assert len(submissions) == 1
        assert callbacks and all(thread == caller for thread, _ in callbacks)
    finally:
        service.close()
