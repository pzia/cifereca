"""#3196: retired W4 audit has no runtime reader, GUI or CLI entrypoint."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.brokers import SynchronizationBrokerImpl

ROOT = Path(__file__).resolve().parents[1]


def test_w4_audit_broker_is_closed_before_any_io():
    class Bomb:
        def __getattr__(self, key):
            raise AssertionError("retired W4 must not touch service or provider")

    broker = object.__new__(SynchronizationBrokerImpl)
    broker.service = Bomb()
    receipt = broker.audit_local_reconciliation("arbitrary-private-credential")
    assert receipt.ok is False
    assert receipt.changed == 0
    assert receipt.facts == {
        "phase": "reconcile",
        "status": "legacy_reconciliation_retired",
        "read_only": True,
        "acquisition_authorized": False,
        "offline": True,
    }


def test_old_w4_audit_is_physically_absent_from_shipped_core():
    assert not (ROOT / "src/cifereca/core/kobo_local_audit.py").exists()
    cli = (ROOT / "src/cifereca/cli.py").read_text(encoding="utf-8")
    gui = (ROOT / "src/cifereca/gui/sync_controller.py").read_text(encoding="utf-8")
    broker = (ROOT / "src/cifereca/brokers.py").read_text(encoding="utf-8")
    assert 'sub.add_parser("kobo-audit"' not in cli
    assert "from .core.kobo_local_audit" not in cli + broker
    assert "audit_local_reconciliation(" not in gui
    assert "Audit SHA local de ce compte" not in gui


def test_canonical_and_generic_paths_still_present():
    source = (ROOT / "src/cifereca/core/source_materialization.py").read_text(
        encoding="utf-8"
    )
    gui = (ROOT / "src/cifereca/gui/sync_controller.py").read_text(encoding="utf-8")
    assert "def _execute_canonical(" in source
    assert "has_complete_delivery_abi(session)" in source
    assert "Télécharger les livres manquants…" in gui
