"""#5069 B9: read-only maintenance preview, never an apply permit."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from cifereca.updater_retention import RetentionObject, plan_retention
from cifereca.updater_retention_maintenance import retention_maintenance_plan


def _fixture():
    # Seven releases generated during the 24 h union window: all are
    # retained, even when a default minimum of 2 would be enough otherwise.
    base = 1_900_000_000
    young = plan_retention(
        [RetentionObject(
            object_id=f"release:{i}", store="releases", unit="core",
            generation=f"release:{i}", installed_at=base - i * 60,
            owned_by_updater=True,
        ) for i in range(7)],
        now=base,
    )
    aged = plan_retention(
        [RetentionObject(
            object_id=f"tx:{i}", store="transactions", unit="core",
            generation=f"tx:{i}", installed_at=base - 90_000 - i * 60,
            owned_by_updater=True, pinned=i == 6,
        ) for i in range(7)],
        now=base,
    )
    return {
        "schema": "cifereca.updater.retention-inventory-combined/1",
        "safe_to_apply": False,
        "plans": {
            "core_transactions": young,
            "plugin_versions": aged,
            "known_persistent_staging": None,
            "updater_backups": None,
            "core_preactivation_backups": None,
        },
        "coverage": ["releases", "transactions", "plugin_versions"],
        "pending_stores": ["persistent_staging", "updater_backups"],
        "blockers": ["partial_store_coverage", "cross_store_references_unqualified"],
    }


def test_seven_young_and_seven_old_still_never_authorize_apply():
    report = retention_maintenance_plan(_fixture())
    assert report["schema"] == "cifereca.updater.maintenance-plan/1"
    assert report["mode"] == "dry_run"
    assert report["safe_to_apply"] is False
    assert report["apply_supported"] is False
    assert report["stores"]["core_transactions"]["total"] == 7
    assert report["stores"]["core_transactions"]["candidates"] == 0
    assert report["stores"]["plugin_versions"]["candidates"] == 4
    assert report["stores"]["plugin_versions"]["retained"] == 3
    assert report["stores"]["known_persistent_staging"]["known"] is False
    assert "separate_supervised_apply_required" in report["blockers"]
    assert report["next_action"] == "run_maintenance_all_plan"
    assert len(report["plan_sha256"]) == 64
    assert "release:0" not in repr(report)
    assert "tx:0" not in repr(report)


def test_digest_changes_when_pin_or_reference_changes():
    original = _fixture()
    one = retention_maintenance_plan(original)
    two = deepcopy(original)
    two["plans"]["plugin_versions"]["objects"][0]["reasons"].append("pinned")
    changed = retention_maintenance_plan(two)
    assert one["plan_sha256"] != changed["plan_sha256"]
    assert {key: {field: value for field, value in store.items()
                  if field != "retention_reasons"}
            for key, store in one["stores"].items()} == {
                key: {field: value for field, value in store.items()
                      if field != "retention_reasons"}
                for key, store in changed["stores"].items()
            }
    assert changed["stores"]["plugin_versions"]["retention_reasons"]["pinned"] == (
        one["stores"]["plugin_versions"]["retention_reasons"]["pinned"] + 1
    )
    assert retention_maintenance_plan(original) == one


@pytest.mark.parametrize("alter", [
    lambda data: data.update(safe_to_apply=True),
    lambda data: data.update(plans={}),
    lambda data: data["plans"]["core_transactions"].update(safe_to_apply=True),
    lambda data: data["plans"]["core_transactions"].update(candidates=2),
    lambda data: data["plans"]["core_transactions"].update(total=100),
    lambda data: data["plans"].update(unknown={"schema": "x"}),
    lambda data: data.update(blockers=["invalid\nSECRET"]),
    lambda data: data["plans"]["core_transactions"].update(objects=[]),
])
def test_foreign_or_incomplete_report_fails_closed(alter):
    candidate = deepcopy(_fixture())
    alter(candidate)
    with pytest.raises(ValueError):
        retention_maintenance_plan(candidate)


def test_cli_declares_dedicated_readonly_mode_not_disposal():
    source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/updater.py"
    ).read_text(encoding="utf-8")
    assert 'p.add_argument("--retention-plan"' in source
    assert "result = retention_maintenance_plan(result)" in source
    assert "or (a.retention_inspect and a.retention_plan)" in source
    assert "--retention-inspect/--retention-plan sont exclusifs et read-only" in source


def test_official_maintenance_plan_cli_is_pure_and_without_apply(
    tmp_path, monkeypatch, capsys,
):
    import json
    from cifereca import updater

    runtime = tmp_path / "runtime"
    packages = tmp_path / "packages"
    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: packages)
    monkeypatch.setattr(updater, "state_dir", lambda **_kw: tmp_path / "state")
    assert updater.main(["--retention-plan", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "cifereca.updater.maintenance-plan/1"
    assert result["safe_to_apply"] is False
    assert result["apply_supported"] is False
    assert result["stores"]["core_transactions"]["known"] is True
    assert not runtime.exists() and not packages.exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("invalid", [
    ["--retention-inspect"], ["--rollback"], ["--github", "main"],
    ["--acknowledge", "a" * 64], ["--recovery-reconcile", "id"],
    ["--dry-run"], ["--status"],
])
def test_retention_plan_never_dispatches_another_command(
    tmp_path, monkeypatch, invalid,
):
    from cifereca import updater

    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "state_dir", lambda **_kw: tmp_path / "state")
    monkeypatch.setattr(
        updater, "install_github",
        lambda *a, **kw: pytest.fail("unexpected update"),
    )
    monkeypatch.setattr(
        updater, "rollback",
        lambda *a, **kw: pytest.fail("unexpected rollback"),
    )
    with pytest.raises(SystemExit) as err:
        updater.main(["--retention-plan", *invalid, "--json"])
    assert err.value.code == 2
    assert not (tmp_path / "runtime").exists()


def test_compact_maintenance_reason_counts_are_derived_from_existing_plan():
    report = retention_maintenance_plan(_fixture())
    current = report["stores"]["core_transactions"]
    old = report["stores"]["plugin_versions"]
    assert current["retention_reasons"]["within_24h"] == 7
    assert current["retention_reasons"]["minimum_generations"] == 2
    assert old["retention_reasons"]["pinned"] == 1
    assert old["retention_reasons"]["within_24h"] == 0
    assert all(type(value) is int for value in old["retention_reasons"].values())
    assert all(value >= 0 for value in old["retention_reasons"].values())
