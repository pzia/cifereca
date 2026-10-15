"""CoreUI P0 #1051: exact identity statistics presentation without Gtk imports."""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.statistics_presentation_controller import (
    refresh_statistics,
    show_statistics,
    statistics_button_label,
    statistics_lines,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "statistics_presentation_controller.py"
MAIN = ROOT / "main_window.py"


def _stats(**updates):
    values = {
        "local_books": 12,
        "local_placements": 15,
        "known_loans": None,
        "loan_sources_observed": 2,
        "remote_owned_rights": 3,
        "formats": {"epub": 9, "pdf": 3},
        "sources": {"Z": 5, "A": 7},
        "epub_covers": {"embedded": 8, "none": 4},
    }
    values.update(updates)
    return values


class Dialog:
    def __init__(self, window, title, body, events):
        self.window = window
        self.title = title
        self.body = body
        self.events = events
        self.responses = []

    def add_response(self, ident, label):
        self.responses.append((ident, label))
        self.events.append(("response", ident, label))

    def present(self):
        self.events.append(("present",))


def _window(stats=None, identity=None, *, button=True):
    events = []
    current = identity if identity is not None else NS(
        identity_id="id-alice", label="Alice"
    )
    values = _stats() if stats is None else stats

    def read(identity_id):
        events.append(("snapshot", identity_id))
        return values

    service = NS(current_identity=current, identity_statistics=read)
    window = NS(_closed=False, service=service)
    if button:
        window.statistics_button = NS(
            set_label=lambda label: events.append(("label", label))
        )

    dialogs = []

    def new_dialog(owner, title, body):
        events.append(("create", title, body))
        d = Dialog(owner, title, body, events)
        dialogs.append(d)
        return d

    Adw = NS(MessageDialog=NS(new=new_dialog))
    return window, Adw, events, dialogs


def test_owner_remains_headless_with_stable_mainwindow_methods():
    src = OWNER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item for item in imports)
    window = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in window.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    for facade, owner in (
        ("_refresh_statistics", "refresh_statistics"),
        ("_show_statistics", "show_statistics"),
    ):
        method = next(node for node in cls.body
                      if isinstance(node, ast.FunctionDef) and node.name == facade)
        assert [arg.arg for arg in method.args.args] == ["self"]
        assert f"statistics_presentation_controller.{owner}" in ast.unparse(method)


def test_button_preserves_zero_loans_distinct_from_unknown():
    assert statistics_button_label(_stats(known_loans=None)) == "12 livres · — prêts de bibliothèque"
    assert statistics_button_label(_stats(known_loans=0)) == "12 livres · 0 prêts de bibliothèque"
    assert statistics_button_label(_stats(known_loans=8)) == "12 livres · 8 prêts de bibliothèque"


def test_detail_preserves_order_french_labels_and_sorted_group_values():
    lines = statistics_lines(_stats())
    assert lines == [
        "Livres locaux distincts : 12",
        "Emplacements locaux indexés présents : 15",
        "Prêts de bibliothèque connus : non actualisés",
        "Sources de prêts observées : 2",
        "Droits d’achat distants actifs : 3",
        "Formats : epub 9, pdf 3",
        "Sources locales : A 7, Z 5",
        "Couvertures EPUB : embedded 8, none 4",
        "Inventaire des fichiers issu du dernier scan ; prêts de bibliothèque issus des dernières réponses, "
        "éventuellement partielles. Les livres achetés en ligne ne sont pas des emprunts ni des fichiers locaux. "
        "Les statistiques d'actions utilisent la fenêtre indiquée.",
    ]


def test_zero_loans_are_known_in_detailed_dialog_not_unknown():
    lines = statistics_lines(_stats(known_loans=0))
    assert "Prêts de bibliothèque connus : 0" in lines
    assert "Prêts de bibliothèque connus : non actualisés" not in lines


def test_empty_format_source_and_cover_buckets_are_explicit():
    lines = statistics_lines(_stats(formats={}, sources={}, epub_covers={}))
    assert "Formats : —" in lines
    assert "Sources locales : —" in lines
    assert "Couvertures EPUB : —" in lines


def test_action_counts_follow_original_order_and_default_to_zero():
    lines = statistics_lines(_stats(actions={
        "acquisitions_succeeded": 4, "acquisitions_failed": 1,
        "synchronizations_failed": 3, "transfers_succeeded": 2,
    }))
    idx = lines.index("Actions sur la période :")
    assert lines[idx:idx + 6] == [
        "Actions sur la période :",
        "  Acquisitions : 4 réussies · 1 échouées",
        "  Imports : 0 réussis · 0 échoués",
        "  Synchronisations : 0 réussies · 3 échouées",
        "  Transformations : 0 réussies · 0 échouées",
        "  Transferts : 2 réussis · 0 échoués",
    ]


def test_empty_actions_do_not_invent_a_reporting_period():
    lines = statistics_lines(_stats(actions={}))
    assert "Actions sur la période :" not in lines
    assert not any(line.startswith("  Fenêtre : ") for line in lines)


def test_dates_are_only_shown_when_attested_and_keep_local_display_format():
    start = 1_700_000_000
    end = start + 3600
    lines = statistics_lines(_stats(
        actions={"from_timestamp": start, "to_timestamp": end},
        loans_observed_at=start,
    ))
    formatter = lambda epoch: datetime.fromtimestamp(epoch).strftime("%d/%m/%Y %H:%M")
    assert "  Fenêtre : " + formatter(start) + " → " + formatter(end) in lines
    assert "Prêts observés au plus tôt : " + formatter(start) in lines
    incomplete = statistics_lines(_stats(actions={"from_timestamp": start}))
    assert not any(row.startswith("  Fenêtre : ") for row in incomplete)


def test_refresh_uses_only_current_identity_and_existing_button():
    window, _adw, events, dialogs = _window()
    refresh_statistics(window)
    assert events == [
        ("snapshot", "id-alice"),
        ("label", "12 livres · — prêts de bibliothèque"),
    ]
    assert dialogs == []


def test_missing_button_skips_service_read():
    window, _adw, events, _dialogs = _window(button=False)
    refresh_statistics(window)
    assert events == []


def test_missing_identity_skips_service_read_and_dialog():
    window, adw, events, dialogs = _window()
    window.service.current_identity = None
    refresh_statistics(window)
    show_statistics(window, Adw=adw)
    assert events == []
    assert dialogs == []


def test_show_statistics_reuses_one_snapshot_and_one_native_dialog():
    window, adw, events, dialogs = _window(_stats(known_loans=0))
    show_statistics(window, Adw=adw)
    assert events[0] == ("snapshot", "id-alice")
    assert events[1][0:2] == ("create", "Statistiques · Alice")
    assert events[-2:] == [("response", "close", "Fermer"), ("present",)]
    assert len(dialogs) == 1
    assert dialogs[0].window is window
    assert dialogs[0].body == "\n".join(statistics_lines(_stats(known_loans=0)))
    assert events.count(("snapshot", "id-alice")) == 1


@pytest.mark.parametrize("func", ["refresh", "show"])
def test_closed_window_skips_all_services_widgets_and_dialogs(func):
    window = NS(_closed=True)
    if func == "refresh":
        refresh_statistics(window)
    else:
        show_statistics(window, Adw=None)


def test_refresh_does_not_touch_destroyed_button_if_snapshot_closes_window():
    window, _adw, events, _dialogs = _window()
    window.service.identity_statistics = lambda identity: (
        setattr(window, "_closed", True) or _stats()
    )
    window.statistics_button.set_label = Mock(
        side_effect=AssertionError("must not update destroyed Gtk label")
    )
    refresh_statistics(window)
    window.statistics_button.set_label.assert_not_called()


def test_dialog_does_not_present_after_window_closes_during_snapshot():
    window, adw, events, dialogs = _window()
    window.service.identity_statistics = lambda identity: (
        setattr(window, "_closed", True) or _stats()
    )
    show_statistics(window, Adw=adw)
    assert events == []
    assert dialogs == []


def test_refresh_does_not_mutate_statistics_mapping():
    values = _stats(actions={"acquisitions_succeeded": 2})
    before = {key: (value.copy() if isinstance(value, dict) else value)
              for key, value in values.items()}
    window, adw, events, dialogs = _window(values)
    refresh_statistics(window)
    show_statistics(window, Adw=adw)
    assert values == before
