"""Exact transaction Paris/ADEPT CLI audit: read-only and source-preserving."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import cli
from tests.test_core_0976_historical_origin_audit import _acq, _right


class OfflineEvidenceStorage:
    def __init__(self, rights, acquisitions):
        self.rights = rights
        self.ledger = acquisitions
        self.calls = []

    def entitlements(self, *, provider_id):
        assert provider_id == "paris"
        self.calls.append("entitlements")
        return self.rights

    def acquisitions(self, *, provider_id):
        assert provider_id == "paris"
        self.calls.append("acquisitions")
        return self.ledger


def _run(monkeypatch, capsys, rights, acquisitions, *, loan="shared", item="book"):
    storage = OfflineEvidenceStorage(rights, acquisitions)

    def no_network():
        raise AssertionError("offline exact audit must never connect to Paris")

    core = SimpleNamespace(
        library=SimpleNamespace(connect_configured=no_network),
        storage=storage, close=lambda: None,
    )
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_: core)
    assert cli.main([
        "paris-adept", "audit-origin",
        "--loan-id", loan, "--item-id", item,
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert storage.calls == ["entitlements", "acquisitions"]
    return report


def test_cli_exact_transaction_retains_both_paris_sources_without_adept_mutation(
    monkeypatch, capsys,
):
    rights = [_right("alice", "paris-a"), _right("bob", "paris-b")]
    claims = [
        _acq("alice", "paris-a", "historical-hb", state="attested"),
        _acq("bob", "paris-b", "historical-magali", state="recorded"),
        _acq("alice", "paris-a", "unrelated", loan="different"),
        _acq("alice", "paris-a", "unrelated", item="different"),
    ]
    report = _run(monkeypatch, capsys, rights, claims)
    assert report["shared_visibility"] is True
    assert report["profile_disagreement"] is True
    assert report["origin_resolution"] == "conflicting_records"
    assert report["status"] == "inventory_attested"  # best single observation
    assert len(report["sources"]) == 2
    assert len(report["origins"]) == 2
    assert {x["recorded_adept_profile_id"] for x in report["origins"]} == {
        "historical-hb", "historical-magali",
    }
    assert {x["historic_adobe_loan_state"] for x in report["origins"]} == {
        "active", "unknown",
    }


def test_cli_exact_transaction_never_uses_item_or_title_as_a_fuzzy_match(
    monkeypatch, capsys,
):
    report = _run(
        monkeypatch, capsys,
        [_right("alice", "paris-a", item="other-item")],
        [_acq("alice", "paris-a", "wrong", item="other-item")],
        item="book",
    )
    assert report["status"] == "unknown"
    assert report["origin_resolution"] == "unknown"
    assert report["sources"] == []
    assert report["origins"] == []


@pytest.mark.parametrize(("loan", "item"), [
    (" ", "book"), ("shared", "  "), ("\x00bad", "book"), ("shared", "\x00bad"),
])
def test_cli_rejects_unusable_exact_keys_without_opening_core(
    monkeypatch, loan, item,
):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("invalid audit args must not open Core")
        ),
    )
    with pytest.raises(SystemExit) as exc:
        cli.main([
            "paris-adept", "audit-origin",
            "--loan-id", loan, "--item-id", item,
        ])
    assert exc.value.code == 2
