"""#5056: no provider name can select the private-search owner lookup."""
from types import SimpleNamespace as NS

from cifereca.core.search_orchestration import _private_search_owner


class OwnerStore:
    def __init__(self):
        self.calls = []
    def identity_for_connection(self, provider, credential):
        self.calls.append((provider, credential))
        return "reader-1"


def test_owner_lookup_uses_exact_session_provider_and_credential():
    storage = OwnerStore()
    service = NS(storage=storage)
    for provider in ("kobo", "numilog", "provider.future"):
        session = NS(provider_id=provider, credential_id="private-account")
        assert _private_search_owner(service, session) == "reader-1"
    assert storage.calls == [
        ("kobo", "private-account"),
        ("numilog", "private-account"),
        ("provider.future", "private-account"),
    ]


def test_unknown_or_broken_owner_fails_closed_without_private_lookup():
    storage = OwnerStore()
    service = NS(storage=storage)
    for provider, credential in ((None, "private"), ("", "private"),
                                 ("kobo", None), ("kobo", "")):
        assert _private_search_owner(
            service, NS(provider_id=provider, credential_id=credential)
        ) is None
    assert storage.calls == []

    class FailingStorage:
        def identity_for_connection(self, *_args):
            raise LookupError("private-account-secret")

    assert _private_search_owner(
        NS(storage=FailingStorage()),
        NS(provider_id="provider.future", credential_id="private"),
    ) is None
