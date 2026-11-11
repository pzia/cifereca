"""#3196: retired Kobo 0.7 migration inventory is not an executable route."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca import cli


def test_removed_legacy_inventory_modules_are_not_shipped():
    root = Path(__file__).resolve().parents[1] / "src/cifereca/core"
    assert not (root / "legacy_backup_migration_inventory.py").exists()
    assert not (root / "legacy_backup_inventory_cli.py").exists()


def test_removed_cli_is_not_reintroduced(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["legacy-backup-inventory", "--identity", "synthetic"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_canonical_sync_command_remains_available(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["sync", "--help"])
    assert error.value.code == 0
    assert "run" in capsys.readouterr().out
