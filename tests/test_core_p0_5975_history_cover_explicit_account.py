"""#5975 / #5851: history cover worker cannot infer mutable GTK borrower."""
from __future__ import annotations

from types import SimpleNamespace as NS
import pytest

from cifereca.core import library_projection
from cifereca.models import Account
from cifereca.services import LibraryService


def _host():
    host = LibraryService.__new__(LibraryService)
    host.current_account = Account(identity_id="alice", provider_id="synthetic",
                                   credential_id="cred-alice", unique_id="a")
    calls = []
    def forbidden(*_args, **_kwargs):
        calls.append("io")
        pytest.fail("invalid history borrower accessed a private cache or session")
    host.storage = NS(cache_get=forbidden, cache_put=forbidden)
    host._session_for_account = forbidden
    host.diagnostics = NS(add=forbidden)
    return host, calls


def test_history_cover_worker_requires_pinned_account_before_any_io():
    host, calls = _host()
    with pytest.raises(TypeError):
        library_projection.resolve_history_thumbnail(
            host, "ORIGIN", "book-1", "private book",
        )
    assert calls == []


@pytest.mark.parametrize("bad_account", [
    None, object(), NS(identity_id="alice"), Account(identity_id=""),
    Account(identity_id="alice", provider_id=""),
    Account(identity_id="alice", credential_id=""),
    Account(identity_id="alice", unique_id=None),
])
def test_history_cover_worker_refuses_invalid_borrower_before_cache(
    bad_account,
):
    host, calls = _host()
    assert library_projection.resolve_history_thumbnail(
        host, "ORIGIN", "book-1", "private book",
        default_url="fallback", account=bad_account,
    ) == ("fallback", "error")
    assert calls == []


def test_public_facade_pins_selected_account_before_worker_sees_navigation(monkeypatch):
    host, calls = _host()
    admitted = host.current_account
    seen = []
    def worker(service, provider, record_id, title, author, year, default_url,
               *, account):
        seen.append(account)
        host.current_account = Account(identity_id="bob", provider_id="synthetic",
                                       credential_id="cred-bob", unique_id="b")
        return "alice-private-cover", "matched"
    monkeypatch.setattr(library_projection, "resolve_history_thumbnail", worker)
    assert host.resolve_history_thumbnail("ORIGIN", "book-1", "private book") == (
        "alice-private-cover", "matched",
    )
    assert seen == [admitted]
    assert host.current_account.identity_id == "bob"
    assert calls == []
