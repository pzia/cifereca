"""#3104: a fully cancelled queued cover must not start an HTTP request.

The two busy workers and the third queued request are deterministic; the
test uses a local MockTransport, not a public cover source or GTK runtime.
"""
from __future__ import annotations

import threading
import time

import httpx

from cifereca.gui.cover_loader import CoverLoader


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_cancelled_queued_cover_skips_get_and_next_live_request_runs(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=2)
    original = loader.client
    release = threading.Event()
    occupied = threading.Event()
    calls = []
    lock = threading.Lock()
    seen = []
    target_calls = []
    target_done = threading.Event()

    def handler(request):
        name = request.url.path
        with lock:
            calls.append(name)
            if calls.count("/blocking-1") + calls.count("/blocking-2") == 2:
                occupied.set()
        if name in ("/blocking-1", "/blocking-2"):
            assert release.wait(5)
        return httpx.Response(
            200, content=PNG, headers={"content-type": "image/png"},
        )

    loader.client = httpx.Client(transport=httpx.MockTransport(handler))
    original.close()
    try:
        for number in (1, 2):
            loader.load(
                f"https://covers.example.test/blocking-{number}",
                seen.append,
            )
        assert occupied.wait(5), "both workers must be occupied"

        target_url = "https://covers.example.test/cancelled"
        request = loader.load(target_url, target_calls.append)
        request.cancel()
        release.set()

        # Queue accounting is still owned by the real worker; avoid a
        # hanging join if a future regression loses task_done().
        limit = time.monotonic() + 5
        while loader._queue.unfinished_tasks and time.monotonic() < limit:
            time.sleep(0.01)
        assert loader._queue.unfinished_tasks == 0
        assert "/cancelled" not in calls
        assert target_calls == []
        assert len(seen) == 2
        assert loader.stats()["cancelled_callbacks"] >= 1
        assert loader.stats()["inflight_urls"] == 0

        # Clearing _queued must not suppress a later live request for
        # the exact same edition/URL.
        loader.load(
            target_url, lambda path: (
                target_calls.append(path), target_done.set()
            ),
        )
        assert target_done.wait(5)
        assert target_calls == [loader._path(target_url)]
        assert calls.count("/cancelled") == 1
    finally:
        release.set()
        loader.close()
