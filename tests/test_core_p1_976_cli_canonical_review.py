"""#976: canonical links can be inspected through CLI, never fulfilled."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import cli
from cifereca.storage import Storage
from tests.test_core_p1_976_canonical_originlink_review import (
    _artifact, _link, _source,
)


def _cli(monkeypatch, capsys, storage, *, loan="paris-loan", item="item-1"):
    def no_connection(*args, **kwargs):
        raise AssertionError("canonical origin review must never contact a provider")

    core = SimpleNamespace(
        storage=storage,
        library=SimpleNamespace(connect_configured=no_connection),
        close=lambda: None,
    )
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_: core)
    assert cli.main([
        "paris-adept", "audit-canonical",
        "--loan-id", loan, "--item-id", item,
    ]) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_reviews_exact_two_borrowers_with_same_sha_without_licence_transfer(
    tmp_path, monkeypatch, capsys,
):
    storage = Storage(tmp_path / "canonical.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "source-a", identity="alice", credential="paris-a")
            _source(db, "source-b", identity="bob", credential="paris-b")
            _link(db, "provider-a", "provider", "source-a", "alice",
                  proof="PRIVATE-PROVIDER-A")
            _link(db, "adept-a", "adept", "source-a", "alice",
                  proof="PRIVATE-ADOBE-A")
            _link(db, "provider-b", "provider", "source-b", "bob",
                  proof="PRIVATE-PROVIDER-B")
            _link(db, "adept-b", "adept", "source-b", "bob",
                  proof="PRIVATE-ADOBE-B")
            before_counts = (
                db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0],
            )
        report = _cli(monkeypatch, capsys, storage)
        assert report["link_resolution"] == "paired_links_unverified"
        assert report["source_count"] == 2
        assert report["multiple_sources"] is True
        assert not report["return_authorized"]
        assert report["adobe_profile_resolution"] == "unknown"
        assert "PRIVATE-" not in repr(report)
        assert {x["source_reference_id"] for x in report["sources"]} == {
            "source-a", "source-b",
        }
        with storage._lock, storage._connect() as db:
            after_counts = (
                db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0],
            )
        assert after_counts == before_counts
    finally:
        storage.close()


def test_cli_does_not_fuzzy_match_other_loan_or_item(tmp_path, monkeypatch, capsys):
    storage = Storage(tmp_path / "scoped.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "source-other", right="different-loan", item="item-1")
            _link(db, "provider-other", "provider", "source-other", "alice")
            _link(db, "adept-other", "adept", "source-other", "alice")
        report = _cli(monkeypatch, capsys, storage)
        assert report["link_resolution"] == "unknown"
        assert report["sources"] == []
        assert report["review_reasons"] == ["canonical_source_reference_missing"]
    finally:
        storage.close()


@pytest.mark.parametrize(("loan", "item"), [
    ("", "item-1"), ("paris-loan", " "), ("paris-loan", "\x00item"),
])
def test_cli_invalid_exact_keys_fail_before_core_open(monkeypatch, loan, item):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("invalid canonical CLI must not open Core")
        ),
    )
    with pytest.raises(SystemExit) as exc:
        cli.main([
            "paris-adept", "audit-canonical",
            "--loan-id", loan, "--item-id", item,
        ])
    assert exc.value.code == 2
