"""Maintenance progress and task type labels must remain closed and private-safe."""
from __future__ import annotations

def test_reconciliation_label_is_closed_allowlist_value():
    pending, rendered = [], []
    line = MaintenanceLineProgress(
        idle_add=pending.append,
        render=rendered.append,
        activity_label="Réconciliation",
    )
    ticket = line.begin()
    line.confirmation(ticket)
    line.running(ticket)
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation en cours…"
    line.report(ticket, message="/PRIVATE/NAME", current=4, total=10)
    # A fabricated plugin total must not bypass the closed phase allowlist.
    assert not pending
    assert rendered[-1].label == "Réconciliation en cours…"
    assert rendered[-1].fraction is None
    assert "/PRIVATE" not in rendered[-1].label


def test_trace_kind_allowlist_remains_closed_without_private_plugin_labels():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/task_runner.py").read_text(encoding="utf-8")
    assert '"maintenance-plan", "maintenance-tool"' in source
    assert 'else "other"' in source
