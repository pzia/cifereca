"""#3185: historical reconciliation is inspection-only, never a maintenance tool."""
import json
from pathlib import Path

from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl


def test_retired_reconciliation_not_available_as_maintenance_tool():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "src/plugins/maintenance-library-audit/cifereca-plugin.json")
        .read_text(encoding="utf-8")
    )
    ids = {plugin["id"] for plugin in manifest["plugins"]}
    assert "maintenance.audit.reconciliation" not in ids
    assert "library.reconciliation.audit" not in ids
    assert {"maintenance.cleanup.duplicates",
            "maintenance.cleanup.duplicates.recover"} <= ids
    assert all(
        "library.reconciliation.read" not in plugin.get("requires_brokers", ())
        for plugin in manifest["plugins"]
    )


def test_legacy_reconciliation_broker_exposes_only_read_only_audit():
    # Compatibility broker remains for inspection consumers; unlike the
    # canonical maintenance broker, it cannot authorize or commit a deletion.
    public = {name for name in dir(LibraryReconciliationReadBrokerImpl)
              if not name.startswith("_")}
    assert public == {"audit", "service"}
