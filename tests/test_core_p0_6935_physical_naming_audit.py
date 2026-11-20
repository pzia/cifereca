"""#6935: real naming broker and maintenance consumer over synthetic FS/SQLite."""
from dataclasses import replace
from pathlib import Path
import runpy
import pytest

from cifereca.brokers import LibraryNamingBrokerImpl
from cifereca.plugin_api import MaintenanceContext
from cifereca.plugin_api.brokers import LibraryPhysicalNamingReadBroker
from cifereca.core import library_physical_naming_audit as audit
from tests.test_core_p0_3176_organizer_review import _service, _put_epub
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows


class Control:
    def __init__(self, stop=None):
        self.stop = stop
        self.checks = 0
        self.events = []
    def check_cancelled(self):
        self.checks += 1
        if self.checks == self.stop:
            raise RuntimeError("cancelled")
    def report(self, **kwargs):
        self.events.append(kwargs)


class Context:
    def __init__(self, naming):
        self.naming = naming
    def broker(self, name):
        assert name == "library.naming", "no notice-only fallback"
        return self.naming


def _tool(broker):
    plugin = (Path(__file__).resolve().parents[1] /
              "src/plugins/maintenance-library-audit/cifereca_library_audits.py")
    return runpy.run_path(str(plugin))["NamingAuditTool"]().activate(Context(broker))


def test_scoped_physical_plan_and_run(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    register(store, "B", "foreign", "card-B")
    good, _ = _put_epub(store, root, "Acquisitions/Autrice - Roman commun.epub")
    bad, _ = _put_epub(store, root, "Acquisitions/wrong.kepub.epub")
    missing, missing_id = _put_epub(store, root, "Acquisitions/absent.epub")
    ambiguous, _ = _put_epub(store, root, "Acquisitions/no-description.epub", catalogue=False)
    tampered, _ = _put_epub(store, root, "Acquisitions/changed.epub")
    excluded, _ = _put_epub(store, root, "Outbox/reader/no-audit.epub")
    foreign, _ = _put_epub(store, root, "Acquisitions/foreign.epub", owner="B")
    outside, _ = _put_epub(store, root, "../B/outside.epub")
    # The recorded foreign-root path is never opened or exposed.
    missing.unlink()
    tampered.write_bytes(b"changed")
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET state='missing' WHERE placement_id=?", (missing_id,))
    broker = LibraryNamingBrokerImpl(_service(store, root))
    assert isinstance(broker, LibraryPhysicalNamingReadBroker)
    tool = _tool(broker)
    before = rows(store)
    paths = [good, bad, ambiguous, tampered, excluded, foreign, outside]
    contents = [path.read_bytes() for path in paths]
    control = Control()
    plan = tool.plan_with_progress(MaintenanceContext(scope="library", identity_id="A"), control)
    assert plan.facts == {
        "physical_schema": 1, "status": "partial", "total": 7,
        "conforming": 1, "deviating": 1, "missing": 1, "unavailable": 3,
        "ambiguous": 1, "unexamined": 0,
    }
    result = tool.run(plan, Control())
    assert not result.ok and result.changed == 0 and result.facts == plan.facts
    assert "missing" in " ".join(plan.warnings)
    assert control.events[-1]["current"] == 7
    assert str(root) not in str((plan, result, control.events))
    assert "foreign.epub" not in str((plan, result, control.events))
    assert rows(store) == before
    assert [path.read_bytes() for path in paths] == contents
    assert not missing.exists()


def test_cancellation_and_scope_revocation(store, tmp_path):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "library"
    _put_epub(store, root, "Autrice - Roman commun.epub")
    service = _service(store, root)
    broker = LibraryNamingBrokerImpl(service)
    assert broker.audit_placements(identity_id="B")["status"] == "unavailable"
    with pytest.raises(RuntimeError, match="cancelled"):
        broker.audit_placements(identity_id="A", control=Control(stop=3))
    class Revoked(Control):
        def report(self, **kwargs):
            super().report(**kwargs)
            service._account_context_epoch += 1
    assert broker.audit_placements(identity_id="A", control=Revoked()) == audit.unavailable_naming_audit()


def test_budget_partial_and_reject_poisoned_plan(store, tmp_path, monkeypatch):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "library"
    _put_epub(store, root, "Autrice - Roman commun.epub")
    broker = LibraryNamingBrokerImpl(_service(store, root))
    tool = _tool(broker)
    ctx = MaintenanceContext(scope="library", identity_id="A")
    clean = tool.plan(ctx)
    assert clean.facts["status"] == "complete"
    assert tool.run(clean, Control()).ok
    for poison in (
        replace(clean, summary="PRIVATE_PATH"),
        replace(clean, facts={**clean.facts, "conforming": True}),
        replace(clean, facts={**clean.facts, "path": "PRIVATE_PATH"}),
        replace(clean, warnings=("PRIVATE_PATH",)),
    ):
        refused = tool.run(poison, Control())
        assert not refused.ok and refused.changed == 0
        assert refused.facts == {"invalid_plan": 1}
        assert "PRIVATE_PATH" not in str(refused)
    monkeypatch.setattr(audit, "_MAX_BYTES", 1)
    limited = tool.plan(ctx)
    assert limited.facts["status"] == "partial" and limited.facts["unexamined"] == 1
    assert not tool.run(limited, Control()).ok
