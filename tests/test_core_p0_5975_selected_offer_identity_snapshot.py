"""#5975: a Library selected offer cannot switch owner mid-selection."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.models import Account
from cifereca.core import selected_source_offer as selection


class Session:
    selected_purchase_branch = "selected"


class Service:
    def __init__(self):
        self.reads = 0
        self.session = Session()
        self.accounts = [
            Account(identity_id="alice", provider_id="future-store",
                    credential_id="credential-A", unique_id="account-A"),
            Account(identity_id="bob", provider_id="future-store",
                    credential_id="credential-B", unique_id="account-B"),
        ]
        self._sessions = {
            "future-store:credential-A": self.session,
            "future-store:credential-B": Session(),
        }
        self._session_key = lambda provider, credential: f"{provider}:{credential}"

    @property
    def current_identity(self):
        self.reads += 1
        if self.reads > 1:
            raise AssertionError("Library target selection re-read mutable GTK navigation")
        return NS(identity_id="alice")


def _entry():
    return {"possessions": [{
        "identity_id": "alice", "provider_id": "future-store",
        "account_id": "account-A", "item_id": "edition-1",
        "kind": "owned", "state": "active",
    }]}


def test_library_selected_offer_pins_one_identity_without_second_gtk_read(monkeypatch):
    service = Service()
    seen = []
    def resolve(_service, target):
        seen.append(target)
        return target, service.accounts[0]
    monkeypatch.setattr(selection, "_resolve", resolve)
    target = selection.select_library_source_offer_target(service, _entry())
    assert type(target) is selection.SourceOfferTarget
    assert target.identity_id == "alice"
    assert target.credential_id == "credential-A"
    assert service.reads == 1
    assert seen == [target]


def test_explicit_selected_offer_identity_never_reads_navigation(monkeypatch):
    service = Service()
    monkeypatch.setattr(selection, "_resolve", lambda _service, target: (target, service.accounts[0]))
    target = selection.select_source_offer_target(
        service, provider_id="future-store", credential_id="credential-A",
        item_id="edition-1", expected_identity_id="alice",
    )
    assert type(target) is selection.SourceOfferTarget
    assert target.identity_id == "alice"
    assert service.reads == 0
    assert selection.select_source_offer_target(
        service, provider_id="future-store", credential_id="credential-B",
        item_id="edition-1", expected_identity_id="alice",
    ) is None
    assert service.reads == 0


def test_missing_or_foreign_library_identity_refuses_without_fallback(monkeypatch):
    service = Service()
    monkeypatch.setattr(selection, "_resolve", lambda *_: (_ for _ in ()).throw(
        AssertionError("foreign source must not reach resolver")
    ))
    assert selection.select_library_source_offer_target(
        service, {"possessions": [{**_entry()["possessions"][0], "identity_id": "bob"}]},
    ) is None
    assert service.reads == 1
