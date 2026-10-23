"""#2804: truthful measured progress when a maintenance scan has no total."""
from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress


def _line():
    queue = []
    rendered = []
    line = MaintenanceLineProgress(
        idle_add=lambda fn: queue.append(fn),
        render=rendered.append,
        activity_label="Traitement",
    )
    def flush():
        while queue:
            queue.pop(0)()
    return line, rendered, flush


def test_unknown_total_shows_count_without_fictitious_percentage():
    line, rendered, flush = _line()
    ticket = line.begin()
    flush()
    line.report(ticket, current=0, total=None, message="/private/title")
    flush()
    assert rendered[-1].phase == "analysis"
    assert rendered[-1].label == "Traitement en cours · 0 élément(s)"
    assert rendered[-1].fraction is None
    line.report(ticket, current=18, total=None, message="SECRET_TOKEN")
    flush()
    assert rendered[-1].label == "Traitement en cours · 18 élément(s)"
    assert "SECRET" not in rendered[-1].label
    assert rendered[-1].fraction is None
    line.confirmation(ticket)
    line.running(ticket)
    flush()
    line.report(ticket, current=600, total=None)
    flush()
    assert rendered[-1].phase == "running"
    assert rendered[-1].label == "Traitement en cours · 600 élément(s)"
    line.finish(ticket, "success")
    flush()
    assert rendered[-1].fraction == 1.0


def test_unknown_total_rejects_invalid_or_unbounded_counts():
    line, rendered, flush = _line()
    ticket = line.begin()
    flush()
    for value in (-1, 1_000_001, True, 1.5, "SECRET"):
        line.report(ticket, current=value, total=None, message="PRIVATE_PATH")
        flush()
        assert rendered[-1].label == "Traitement en cours…"
        assert rendered[-1].fraction is None


def test_unknown_total_coalesces_and_ignores_cancelled_generation():
    line, rendered, flush = _line()
    old = line.begin()
    line.report(old, current=18, total=None)
    current = line.begin()
    line.report(old, current=600, total=None)
    line.report(current, current=3, total=None)
    assert len(rendered) == 0
    flush()
    assert len(rendered) == 1
    assert rendered[-1].label == "Traitement en cours · 3 élément(s)"
    line.finish(current, "cancelled")
    line.report(current, current=42, total=None)
    flush()
    assert rendered[-1].label == "Annulé"
