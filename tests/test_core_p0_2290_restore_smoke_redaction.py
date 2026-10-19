"""P0 #2290: restored-plugin validation must fail closed without leaking names."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import updater


PRIVATE = "https://private.invalid/book.acsm?token=DO_NOT_EXPOSE"
PACKAGE = "org.privateuser"


def _smoke(monkeypatch, tmp_path, rows, *, active=True):
    monkeypatch.setattr(updater, "_command_path", lambda *_args: tmp_path / "cli")
    monkeypatch.setattr(
        updater, "_run_bounded_process",
        lambda *_args, **_kw: SimpleNamespace(
            returncode=0, stdout=json.dumps(rows), stderr=PRIVATE,
        ),
    )
    return updater._smoke_restored_plugins(
        tmp_path, {"packages": {PACKAGE: {"before": {
            "exists": True, "active": "1.0" if active else None,
        }}}},
    )


def test_restored_smoke_does_not_publish_untrusted_contribution_id(monkeypatch, tmp_path):
    with pytest.raises(updater.UpdateError) as failure:
        _smoke(monkeypatch, tmp_path, [{
            "package_id": PACKAGE, "plugin_id": PRIVATE, "state": "error",
        }])
    assert str(failure.value) == "Plugins restaurés incompatibles ou en erreur"
    assert PRIVATE not in str(failure.value)
    assert PACKAGE not in str(failure.value)


def test_restored_smoke_does_not_publish_missing_active_package(monkeypatch, tmp_path):
    with pytest.raises(updater.UpdateError) as failure:
        _smoke(monkeypatch, tmp_path, [], active=True)
    assert str(failure.value) == "Plugins actifs restaurés absents de l'inventaire"
    assert PRIVATE not in str(failure.value)
    assert PACKAGE not in str(failure.value)


@pytest.mark.parametrize("state", [["error"], {"error": True}, None, True])
def test_restored_smoke_refuses_malformed_states_without_type_errors(
    monkeypatch, tmp_path, state,
):
    with pytest.raises(updater.UpdateError) as failure:
        _smoke(monkeypatch, tmp_path, [{
            "package_id": PACKAGE, "plugin_id": PRIVATE, "state": state,
        }])
    assert str(failure.value) == "Validation des plugins restaurés: liste JSON attendue"
    assert PRIVATE not in str(failure.value)


def test_restored_smoke_does_not_require_contribution_for_inactive_package(
    monkeypatch, tmp_path,
):
    result = _smoke(monkeypatch, tmp_path, [], active=False)
    assert result["plugin_count"] == 0


def test_missing_plugin_backup_reports_only_closed_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "installed")
    with pytest.raises(updater.UpdateError) as failure:
        updater._restore_transaction_packages(
            tmp_path, {"packages": {PACKAGE: {"before": {"exists": True}}}},
        )
    assert str(failure.value) == "Backup plugin de restauration absent"
    assert PACKAGE not in str(failure.value)


def test_restored_plugin_mismatch_reports_only_closed_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "installed")
    backup = tmp_path / "plugins" / PACKAGE
    backup.mkdir(parents=True)
    (backup / "manifest.txt").write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(updater, "_package_identity", lambda _id: {"exists": False})
    with pytest.raises(updater.UpdateError) as failure:
        updater._restore_transaction_packages(
            tmp_path, {"packages": {PACKAGE: {"before": {"exists": True}}}},
        )
    assert str(failure.value) == "Vérification de restauration plugin échouée"
    assert PACKAGE not in str(failure.value)
