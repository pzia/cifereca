"""#5851: cached history covers belong to the admitted borrower, not UI navigation."""
from pathlib import Path

from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, SearchResult
from cifereca.services import LibraryService
from cifereca.storage import Storage


class _Config:
    def get(self, _key, default=None):
        return default

    def set(self, *_args):
        pass

    def save(self):
        pass


def _result(url):
    return SearchResult(
        id="synthetic:123", record_id="123", docbase="ORIGIN",
        medium="physical", title="Synthetic", author=None, type="Livre",
        format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=url, friendly_url=None,
        scenario="CATALOGUE",
    )


class _Session:
    def __init__(self, url):
        self.url = url
        self.reads = 0

    def search(self, *_args, **_kwargs):
        self.reads += 1
        return [_result(self.url)]


def _library(tmp_path):
    return LibraryService(
        _Config(), Storage(Path(tmp_path) / "library.sqlite"), Diagnostics(),
    )


def _accounts():
    return (
        Account(identity_id="alice", provider_id="fixture-library",
                credential_id="cred-alice", unique_id="", is_primary=True),
        Account(identity_id="bob", provider_id="fixture-library",
                credential_id="cred-bob", unique_id="", is_primary=True),
    )


def test_history_cover_cache_is_not_shared_between_borrowers(tmp_path):
    service = _library(tmp_path)
    alice, bob = _accounts()
    sessions = {
        "alice": _Session("https://covers.example/ALICE"),
        "bob": _Session("https://covers.example/BOB"),
    }
    seen = []

    def resolve(account):
        seen.append(account)
        return sessions[account.identity_id]

    service._session_for_account = resolve
    try:
        service.current_account = alice
        args = ("ORIGIN", "123", "Synthetic", None, None, None)
        assert service.resolve_history_thumbnail(*args) == (
            "https://covers.example/ALICE", "matched",
        )
        service.current_account = bob
        assert service.resolve_history_thumbnail(*args) == (
            "https://covers.example/BOB", "matched",
        )
        service.current_account = alice
        assert service.resolve_history_thumbnail(*args) == (
            "https://covers.example/ALICE", "matched",
        )
        assert seen == [alice, bob]
        assert (sessions["alice"].reads, sessions["bob"].reads) == (1, 1)
    finally:
        service.close()


def test_navigation_change_during_cache_read_cannot_retarget_session(tmp_path):
    service = _library(tmp_path)
    alice, bob = _accounts()
    service.current_account = alice
    original_get = service.storage.cache_get
    seen = []

    def switching_cache_get(*args, **kwargs):
        service.current_account = bob
        return original_get(*args, **kwargs)

    service.storage.cache_get = switching_cache_get

    def resolve(account):
        seen.append(account)
        return _Session("https://covers.example/ALICE" if account is alice
                        else "https://covers.example/BOB")

    service._session_for_account = resolve
    try:
        assert service.resolve_history_thumbnail(
            "ORIGIN", "123", "Synthetic",
        ) == ("https://covers.example/ALICE", "matched")
        assert seen == [alice]
        assert service.current_account is bob
    finally:
        service.close()


def test_malformed_selected_account_never_reads_cache_or_session(tmp_path):
    service = _library(tmp_path)
    service.current_account = object()
    service.storage.cache_get = lambda *_a, **_kw: (
        (_ for _ in ()).throw(AssertionError("cache access"))
    )
    service._session_for_account = lambda *_a: (
        (_ for _ in ()).throw(AssertionError("session access"))
    )
    try:
        assert service.resolve_history_thumbnail(
            "ORIGIN", "123", "Synthetic", default_url="default",
        ) == ("default", "error")
    finally:
        service.close()
