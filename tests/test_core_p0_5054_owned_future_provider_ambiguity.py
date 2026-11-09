"""P0 #5054: unknown providers must not be silently ignored by legacy actions."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import numilog_owned_download as owned


def _pos(provider, *, identity="alice", kind="owned", state="active"):
    return {
        "provider_id": provider, "identity_id": identity,
        "kind": kind, "state": state, "entitlement_id": "owned-1",
        "item_id": "edition-1",
    }


@pytest.mark.parametrize("other_provider", ["kobo", "future-library", "bnf-pret"])
def test_competing_owned_provider_refuses_numilog_action_before_resolution(
    monkeypatch, other_provider,
):
    calls = []
    monkeypatch.setattr(owned, "_resolve", lambda *_args: calls.append("resolved"))
    service = NS(current_identity=NS(identity_id="alice"))
    entry = {"possessions": [_pos("numilog"), _pos(other_provider)]}
    assert owned.target_for_entry(service, entry) is None
    assert calls == []


def test_exact_single_numilog_right_remains_eligible(monkeypatch):
    marker = object()
    calls = []
    def resolve(_service, entitlement, identity, item):
        calls.append((entitlement, identity, item))
        return marker, object()
    monkeypatch.setattr(owned, "_resolve", resolve)
    service = NS(current_identity=NS(identity_id="alice"))
    entry = {"possessions": [
        _pos("numilog"), _pos("future-library", identity="bob"),
        _pos("future-library", state="expired"),
        _pos("future-library", kind="loan"),
    ]}
    assert owned.target_for_entry(service, entry) is marker
    assert calls == [("owned-1", "alice", "edition-1")]


def test_single_unknown_provider_never_becomes_numilog(monkeypatch):
    calls = []
    monkeypatch.setattr(owned, "_resolve", lambda *_args: calls.append("resolved"))
    service = NS(current_identity=NS(identity_id="alice"))
    assert owned.target_for_entry(
        service, {"possessions": [_pos("future-library")]},
    ) is None
    assert calls == []
