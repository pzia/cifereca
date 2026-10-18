"""#1143: GUI refresh errors never export raw provider exception messages.

Exercise the real headless adapters and their GTK completion callbacks.  No GTK
or provider network needed; explicit UI failures retain safe user messages.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import UserFacingError
from tests.test_core_ui_identity_async_recent import _harness


PRIVATE = "https://private.invalid/file.acsm?token=NEVER_EXPORT path=/home/private/book.epub"


@pytest.mark.parametrize("method,operation,code", [
    ("refresh_library", "library refresh", "library_refresh_failed"),
    ("refresh_recent", "recent refresh", "recent_refresh_failed"),
])
@pytest.mark.parametrize("with_control", [False, True])
def test_refresh_error_never_exports_raw_exception_or_sensitive_class_name(
    method, operation, code, with_control,
):
    host = _harness()
    events, ui = [], []
    host.diagnostics = NS(add=lambda *args, **kwargs: events.append((args, kwargs)))
    host.show_error = lambda *args: ui.append(args)
    control = object() if with_control else None
    getattr(host, method)(control=control)
    request = host.runner.requests[-1]

    class DangerousProviderError(RuntimeError):
        pass

    request.err(DangerousProviderError(PRIVATE))
    assert len(events) == 1
    assert events[0] == (("UI", operation, "error"), {"detail": {"code": code}})
    if with_control:
        assert ui == [(
            "Traitement indisponible",
            "Actualisation de la bibliothèque impossible"
            if method == "refresh_library"
            else "Actualisation des récupérations impossible",
        )]
    else:
        assert ui == []
    exported = json.dumps({"diagnostics": events, "gtk": ui}, ensure_ascii=False)
    assert "NEVER_EXPORT" not in exported
    assert "DangerousProviderError" not in exported
    assert "/home/private/" not in exported
    assert "private.invalid" not in exported
    assert getattr(host, "_library_refresh_pending" if method == "refresh_library"
                   else "_recent_refresh_pending") is False


@pytest.mark.parametrize("method,operation,code", [
    ("refresh_library", "library refresh", "library_refresh_failed"),
    ("refresh_recent", "recent refresh", "recent_refresh_failed"),
])
def test_typed_user_facing_error_keeps_safe_public_message(method, operation, code):
    host = _harness()
    events, ui = [], []
    host.diagnostics = NS(add=lambda *args, **kwargs: events.append((args, kwargs)))
    host.show_error = lambda *args: ui.append(args)
    getattr(host, method)(control=object())
    host.runner.requests[-1].err(
        UserFacingError("Connexion à renouveler", code="signin_required",
                        technical_detail=PRIVATE)
    )
    assert ui[0][0] == "Connexion à renouveler"
    assert events == [(("UI", operation, "error"), {"detail": {"code": code}})]
    assert "NEVER_EXPORT" not in repr((events, ui))


@pytest.mark.parametrize("method", ["refresh_library", "refresh_recent"])
def test_stale_identity_errors_still_emit_no_diagnostic_or_user_message(method):
    host = _harness()
    events, ui = [], []
    host.diagnostics = NS(add=lambda *args, **kwargs: events.append((args, kwargs)))
    host.show_error = lambda *args: ui.append(args)
    getattr(host, method)(control=object())
    old = host.runner.requests[-1]
    host.service.current_identity = NS(identity_id="bob")
    host._identity_context_generation += 1
    old.err(RuntimeError(PRIVATE))
    assert not events
    assert not ui
    assert len(host.runner.requests) == 2
    assert host.runner.requests[-1].work() == [{"identity_id": "bob"}]
