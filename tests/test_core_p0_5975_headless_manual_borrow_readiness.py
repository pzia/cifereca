"""P0 #5975: manual-borrow readiness is headless and owner-scoped.

The diagnostic is read-only; no source authorization or provider GET is inferred.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace as NS

from cifereca.core import manual_borrow_contract as contract
from cifereca.core import manual_library_borrow as manual
from cifereca.services import LibraryService


class PoisonNavigation:
    @property
    def current_identity(self):
        raise AssertionError("Core queried mutable navigation")


def test_missing_owner_is_closed_before_plugin_lookup():
    class Host(PoisonNavigation):
        @property
        def _plugin_manager(self):
            raise AssertionError("missing owner must deny before registry lookup")

    from cifereca.models import SearchResult
    item = SearchResult(
        id="edition", record_id="edition", docbase="FUTURE",
        medium="digital", title="Private", author=None, type=None,
        format=None, publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="synthetic",
        source_provider="future-library", source_credential="card",
    )
    # Even a correctly shaped supplier record cannot force a registry lookup
    # when its selected owner is missing.
    assert manual.manual_borrow_available(
        Host(), item, provider_id="future-library",
        docbase="FUTURE", identity_id=None,
    ) is False
    result = manual.manual_borrow_diagnostic(
        Host(), NS(source_provider="future-library", docbase="FUTURE",
                   medium="digital", source_credential="card"),
        provider_id="future-library", docbase="FUTURE",
        primary_id=None, identity_id=None,
        result_type=NS, source_current=lambda *_: (_ for _ in ()).throw(
            AssertionError("no scope without identity")
        ),
    )
    assert result == {
        "local_action_possible": False,
        "reason_code": "identity_not_selected",
        "provider_eligibility": "unknown",
    }


def test_diagnostic_accepts_explicit_owner_without_navigation():
    host = PoisonNavigation()
    called = []

    def available(_service, _item, **scope):
        called.append(scope)
        scope["diagnostic"]["reason_code"] = "local_action_available_provider_unknown"
        return True

    item = NS(source_provider="fictional", source_credential="card",
              docbase="FICTION", medium="digital")
    result = manual.manual_borrow_diagnostic(
        host, item, provider_id="fictional", docbase="FICTION",
        primary_id="", identity_id="alice", result_type=NS,
        available=available,
        source_current=lambda _host, _item, owner: owner == "alice",
    )
    assert result == {
        "local_action_possible": True,
        "reason_code": "local_action_available_provider_unknown",
        "provider_eligibility": "unknown",
    }
    assert len(called) == 1
    assert called[0]["identity_id"] == "alice"
    assert called[0]["provider_id"] == "fictional"


class Facade:
    def __init__(self):
        self.selected = NS(identity_id="alice")
        self._account_context_epoch = 7
        self.reads = 0

    @property
    def current_identity(self):
        self.reads += 1
        return self.selected


def test_public_availability_blocks_a_b_a_epoch_drift(monkeypatch):
    host = Facade()
    captured = []

    def change(_service, _item, *, identity_id):
        captured.append(identity_id)
        host._account_context_epoch += 2  # A -> B -> A
        return True

    monkeypatch.setattr(contract, "manual_borrow_action_available", change)
    assert LibraryService.can_borrow_manual(host, object()) is False
    assert captured == ["alice"]
    assert host.reads == 2

    monkeypatch.setattr(
        contract, "manual_borrow_action_available",
        lambda _service, _item, *, identity_id: identity_id == "alice",
    )
    assert LibraryService.can_borrow_manual(host, object()) is True
    assert host.reads == 4


def test_public_diagnostic_revokes_stale_positive_before_display(monkeypatch):
    host = Facade()
    def stale(_service, _item, *, identity_id):
        assert identity_id == "alice"
        host._account_context_epoch += 2
        return {
            "local_action_possible": True,
            "reason_code": "local_action_available_provider_unknown",
            "provider_eligibility": "unknown",
        }
    monkeypatch.setattr(contract, "manual_borrow_action_diagnostic", stale)
    assert LibraryService.manual_borrow_diagnostic(host, object()) == {
        "local_action_possible": False,
        "reason_code": "local_scope_changed",
        "provider_eligibility": "unknown",
    }
    assert host.reads == 2


def test_core_readiness_workers_have_no_gtk_navigation_reads():
    for function in (manual.manual_borrow_available,
                     manual.manual_borrow_diagnostic):
        tree = ast.parse(inspect.getsource(function))
        assert not any(isinstance(node, ast.Attribute)
                       and node.attr == "current_identity"
                       for node in ast.walk(tree))
