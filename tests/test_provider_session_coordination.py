"""Session extraction preserves the facade executor and deferred-close lifecycle."""
from cifereca import services
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def test_facade_executor_coalesces_notifications_and_close_blocks_late_refresh(tmp_path, monkeypatch):
    service, _ = service_with_linked_borrower(tmp_path)
    queued = []

    class ManualExecutor:
        def submit(self, callback):
            queued.append(callback)

    def forbidden_refresh(**_kwargs):
        raise AssertionError('closed service must not reconcile or connect providers')

    monkeypatch.setattr(services, '_SEARCH_EXECUTOR', ManualExecutor())
    monkeypatch.setattr(service, 'refresh_virtual_collections', forbidden_refresh)
    try:
        service._queue_virtual_collection_refresh()
        service._queue_virtual_collection_refresh()
        assert len(queued) == 1
        assert service._virtual_refresh_pending and service._virtual_refresh_again
        service.close()
        queued[0]()
        assert not service._virtual_refresh_pending
        assert service._virtual_collection_closed
    finally:
        service.close()
