"""#1346: task feedback must never echo provider exceptions or identity keys."""
from __future__ import annotations

import pytest

from tests.test_core_ui_1132_opening_sync_close_gate import (
    _host as opening_host, _method as opening_method,
)
from tests.test_core_ui_1132_kobo_progress_close_gate import (
    _method as kobo_method,
)


PRIVATE_ERROR = (
    "https://private.invalid/delegate?token=BEARER_PRIVATE "
    "/home/private/books/book.epub account_id=PRIVATE_ACCOUNT"
)


def raise_private(*args, **kwargs):
    raise RuntimeError(PRIVATE_ERROR)


def test_opening_worker_and_diagnostics_do_not_echo_provider_exception():
    start, _scheduled = opening_method()
    host, submitted, _effects = opening_host()
    displays = []
    diagnostics = []
    host.service.opening_sync = raise_private
    host.show_error = lambda *args: displays.append(args)
    host.diagnostics.add = lambda *args, **kwargs: diagnostics.append((args, kwargs))
    start(host, control=object())
    args, _kwargs = submitted[0]
    with pytest.raises(RuntimeError) as exc:
        args[1](lambda label: None)
    # The original exception must already be redacted when it reaches the
    # TaskRunner's global status line, not only when the page calls err().
    assert str(exc.value) == "Synchronisation des bibliothèques indisponible"
    assert exc.value.__cause__ is None
    args[3](exc.value)
    assert displays == [(
        "Synchronisation des bibliothèques indisponible.",
        "Synchronisation impossible",
    )]
    assert diagnostics == [(
        ("SYNC", "opening sync", "error"),
        {"detail": {"reason": "opening_sync_failed"}},
    )]
    assert PRIVATE_ERROR not in repr((exc.value, displays, diagnostics))


def test_opening_progress_without_public_label_never_falls_back_to_identity_key():
    start, _scheduled = opening_method()
    host, submitted, _effects = opening_host()
    progress_handlers = []
    host.service.opening_sync = lambda **kwargs: progress_handlers.append(
        kwargs["progress"]
    )
    start(host)
    updates = []
    submitted[0][0][1](updates.append)
    assert len(progress_handlers) == 1
    progress_handlers[0]({
        "event": "identity_begin",
        "identity_id": "PRIVATE_ACCOUNT",
        "label": "",
    })
    assert updates == ["Synchronisation · une identité…"]
    assert "PRIVATE_ACCOUNT" not in repr(updates)


@pytest.mark.parametrize("manual", [True, False])
def test_kobo_worker_redacts_provider_exception_before_taskrunner(manual):
    run, _queue = kobo_method("run_kobo_backup")
    submitted = []
    displays = []
    diagnostics = []
    host = type("Host", (), {})()
    host._closed = False
    host.service = type("Service", (), {"sync_kobo_backup": staticmethod(raise_private)})()
    host.runner = type("Runner", (), {
        "submit": staticmethod(lambda *args, **kwargs: submitted.append((args, kwargs)))
    })()
    host.show_error = lambda *args: displays.append(args)
    host.diagnostics = type("Diagnostics", (), {
        "add": staticmethod(lambda *args, **kwargs: diagnostics.append((args, kwargs)))
    })()
    run(host, manual=manual)
    args, _kwargs = submitted[0]
    with pytest.raises(RuntimeError) as exc:
        args[1]()
    assert str(exc.value) == "Synchronisation locale Kobo indisponible"
    assert exc.value.__cause__ is None
    args[3](exc.value)
    if manual:
        assert displays == [(
            "Synchronisation locale Kobo indisponible.",
            "Synchronisation Kobo impossible",
        )]
        assert diagnostics == []
    else:
        assert displays == []
        assert diagnostics == [(
            ("KOBO", "automatic backup task", "error"),
            {"detail": {"reason": "kobo_sync_failed"}},
        )]
    assert PRIVATE_ERROR not in repr((exc.value, displays, diagnostics))
