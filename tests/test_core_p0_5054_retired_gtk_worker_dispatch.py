"""#5054: never queue a second provider-specific GET on GTK opening."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import source_inventory_startup as dispatch


def test_no_legacy_numilog_dispatch_reachable_from_startup_gateway():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/source_inventory_startup.py").read_text()
    assert "numilog_materialization_startup" not in source
    assert "materialize(window)" not in source
    assert "synchronize_scheduled_numilog_materialization" not in source


@pytest.mark.parametrize("with_broker", (True, False))
def test_no_due_inventory_does_not_queue_old_numilog_worker(
    monkeypatch, with_broker,
):
    submissions = []
    broker = NS() if with_broker else None
    window = NS(_closed=False, runner=NS(
        submit=lambda *_a, **_k: submissions.append(True),
    ))
    monkeypatch.setattr(dispatch, "_broker", lambda _window: broker)
    monkeypatch.setattr(dispatch, "due_startup_identities",
                        lambda *_a: ())
    assert dispatch.dispatch_after_opening(window) is False
    assert submissions == []
