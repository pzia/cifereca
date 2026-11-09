"""P0 #5069: functional transaction-history GC, synthetic updater-owned trees."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

import pytest

from cifereca import updater
from cifereca.updater_transaction_maintenance import transaction_maintenance

NOW = 2_000_000_000.0


def _stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _runtime(tmp_path):
    root = tmp_path / "runtime"
    for name, days in (("current", 2), ("previous", 3)):
        release = root / "releases" / name
        release.mkdir(parents=True)
        (release / ".cifereca-install.json").write_text(json.dumps({
            "version": name,
            "installed_at": datetime.fromtimestamp(
                NOW - days * 86400, timezone.utc,
            ).isoformat(),
        }))
        os.utime(release, (NOW - days * 86400, NOW - days * 86400))
        updater._replace_symlink(root / name, release)
    return root


def _transaction(root, number, age):
    path = root / "transactions" / f"tx-{number}"
    path.mkdir(parents=True)
    (path / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": path.name, "created_at": _stamp(NOW - age),
        "state": "applied",
        "core": {"before_current": None, "before_previous": None, "after": None},
        "packages": {},
    }))
    (path / "plugins").mkdir()
    (path / "plugins" / "test.txt").write_text("synthetic")
    return path


def test_seven_old_transactions_and_snapshots_reduce_to_two(tmp_path, monkeypatch):
    root = _runtime(tmp_path)
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    paths = [_transaction(root, n, 3 * 86400 + n * 60) for n in range(7)]
    plan = transaction_maintenance(root, now=NOW)
    assert plan["candidate_transactions"] == 5
    result = transaction_maintenance(
        root, apply=True, acknowledge=plan["acknowledgement_sha256"], now=NOW,
    )
    assert result["removed"] == 5
    assert sum(path.is_dir() for path in paths) == 2
    assert paths[0].is_dir() and paths[1].is_dir()
    assert len(list((root / "transaction-gc").glob("*.json"))) == 5
    assert not any(p.is_dir() for p in (root / "transaction-gc").iterdir())


def test_seven_recent_transactions_all_retained(tmp_path):
    root = _runtime(tmp_path)
    for n in range(7):
        _transaction(root, n, n * 60 + 100)
    plan = transaction_maintenance(root, now=NOW)
    assert plan["candidate_transactions"] == 0
    assert plan["safe_to_apply"] is False
    assert not (root / "transaction-gc").exists()


def test_missing_or_wrong_acknowledgement_does_not_mutate(tmp_path):
    root = _runtime(tmp_path)
    paths = [_transaction(root, n, 3 * 86400 + n * 60) for n in range(7)]
    with pytest.raises(ValueError, match="acknowledgement"):
        transaction_maintenance(root, apply=True, now=NOW)
    with pytest.raises(ValueError, match="plan_changed"):
        transaction_maintenance(root, apply=True, acknowledge="0" * 64, now=NOW)
    assert all(path.is_dir() for path in paths)
    assert not (root / "transaction-gc").exists()


def test_unfinished_transaction_refuses_any_gc(tmp_path):
    root = _runtime(tmp_path)
    paths = [_transaction(root, n, 3 * 86400 + n * 60) for n in range(7)]
    journal = paths[6] / "transaction.json"
    tx = json.loads(journal.read_text())
    tx["state"] = "restoration_incomplete"
    journal.write_text(json.dumps(tx))
    plan = transaction_maintenance(root, now=NOW)
    assert (paths[6] / "transaction.json").is_file()
    assert plan["candidate_transactions"] <= 4


def test_cli_plan_readonly_on_missing_runtime(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    assert updater.main(["--maintenance-transactions", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "plan"
    assert report["candidate_transactions"] == 0
    assert not (tmp_path / "runtime").exists()
