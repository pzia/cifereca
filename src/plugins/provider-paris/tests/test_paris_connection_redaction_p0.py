"""Paris P0: upstream connection failures must be diagnosable without secrets."""
from __future__ import annotations

import httpx
import pytest

from cifereca_paris_client import ParisClient


SECRET = "private-cookie-and-signed-url?auth=TOKEN"


class Recorder:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


@pytest.mark.parametrize("category,operation", [
    ("AUTH", "login"),
    ("API", "summary SYRACUSE"),
    ("API", "summary DILICOM"),
])
def test_private_auth_and_summary_errors_are_redacted(category, operation):
    diagnostics = Recorder()
    client = ParisClient(diagnostics=diagnostics)
    try:
        def fail():
            raise RuntimeError(SECRET)
        with pytest.raises(RuntimeError, match="private-cookie"):
            client._timed(category, operation, fail)
        errors = [kwargs.get("detail", {}) for args, kwargs in diagnostics.rows
                  if args[:3] == (category, operation, "error")]
        assert len(errors) == 1
        assert errors[0] == {"error": "RuntimeError"}
        assert SECRET not in repr(diagnostics.rows)
    finally:
        client.close()


def test_login_protocol_retry_never_logs_exception_text(monkeypatch):
    diagnostics = Recorder()
    client = ParisClient(diagnostics=diagnostics)
    def protocol_error(*args, **kwargs):
        raise httpx.RemoteProtocolError(SECRET)
    try:
        monkeypatch.setattr(client.client, "post", protocol_error)
        monkeypatch.setattr(client, "_reset_http_client", lambda profile: None)
        monkeypatch.setattr("cifereca_paris_client.time.sleep", lambda *_: None)
        with pytest.raises(httpx.RemoteProtocolError):
            client.login("synthetic-user", "synthetic-password")
        assert SECRET not in repr(diagnostics.rows)
        labels = [args[1] for args, kwargs in diagnostics.rows]
        assert "login retry" in labels
        assert "login retry exhausted" in labels
        for args, kwargs in diagnostics.rows:
            if args[1] in {"login retry", "login retry exhausted"}:
                assert "message" not in kwargs.get("detail", {})
    finally:
        client.close()
