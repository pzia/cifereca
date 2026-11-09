"""#5069: one acknowledged maintenance sweep, no newly eligible implicit purge."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca import updater
from cifereca import updater_maintenance_all as maintenance


def test_six_store_plan_is_read_only_on_missing_runtime(tmp_path):
    roots = tuple(tmp_path / name for name in ("runtime", "packages", "dev", "core"))
    result = maintenance.maintenance_all(*roots)
    assert result["mode"] == "plan"
    assert result["safe_to_apply"] is False
    assert set(result["stores"]) == {
        "releases", "transactions", "plugins", "dev_backups", "core_backups",
        "staging",
    }
    assert all(row["candidates"] == 0 for row in result["stores"].values())
    assert all(not path.exists() for path in roots)


def test_unified_maintenance_exact_plan_and_dependency_reclassification(
    tmp_path, monkeypatch,
):
    events = []
    mutation = {"transactions": False}
    names = ("releases", "transactions", "plugins", "dev_backups",
             "core_backups", "staging")
    services = []

    for name in names:
        def executor(*args, apply=False, acknowledge=None, now=None,
                     name=name, **kwargs):
            if apply:
                events.append(("apply", name, acknowledge))
                assert acknowledge == name[0] * 64
                if name == "releases":
                    mutation["transactions"] = True
                return {"removed": 1}
            events.append(("plan", name, None))
            candidate = 1
            proof = name[0] * 64
            if name == "transactions" and mutation["transactions"]:
                candidate = 2
            return {"mode": "plan", "acknowledgement_sha256": proof,
                    "candidate": candidate}
        services.append((name, executor, (), {}, "candidate"))

    monkeypatch.setattr(maintenance, "_stores", lambda *args, **kw: services)
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    roots = tuple(tmp_path / name for name in ("runtime", "packages", "dev", "core"))
    plan = maintenance.maintenance_all(*roots)
    assert len(plan["acknowledgement_sha256"]) == 64
    applied = maintenance.maintenance_all(
        *roots, apply=True, acknowledge=plan["acknowledgement_sha256"],
    )
    assert applied["stores"]["releases"] == {"status": "complete", "removed": 1}
    assert applied["stores"]["transactions"] == {
        "status": "deferred", "removed": 0, "reason": "plan_changed",
    }
    assert applied["removed"] == 5
    assert not any(name == "transactions" for action, name, _ in events
                   if action == "apply")


def test_changed_acknowledgement_does_not_start_purge(tmp_path, monkeypatch):
    events = []

    def fake(*args, apply=False, **kwargs):
        events.append(apply)
        return {"mode": "plan", "candidate": 1,
                "acknowledgement_sha256": "a" * 64}

    monkeypatch.setattr(
        maintenance, "_stores",
        lambda *args, **kw: [("releases", fake, (), {}, "candidate")],
    )
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    roots = tuple(tmp_path / name for name in ("runtime", "packages", "dev", "core"))
    with pytest.raises(ValueError, match="plan_changed"):
        maintenance.maintenance_all(
            *roots, apply=True, acknowledge="0" * 64,
        )
    assert events == [False]


def test_cli_global_maintenance_plan_no_roots_created(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "packages")
    monkeypatch.setattr(updater, "state_dir", lambda create=False: tmp_path / "state")
    assert updater.main(["--maintenance-all", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "cifereca.updater.maintenance-all/1"
    assert report["mode"] == "plan"
    assert len(report["stores"]) == 6
    assert not (tmp_path / "runtime").exists()
    assert not (tmp_path / "state").exists()


def test_unified_apply_requires_explicit_acknowledgement(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())
    roots = tuple(tmp_path / name for name in ("runtime", "packages", "dev", "core"))
    with pytest.raises(ValueError, match="acknowledgement_required"):
        maintenance.maintenance_all(*roots, apply=True)
    assert all(not path.exists() for path in roots)
