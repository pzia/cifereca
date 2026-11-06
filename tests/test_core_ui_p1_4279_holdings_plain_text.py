"""Regression for provider-controlled holdings text rendered by CoreUI (#4279).

The extracted GUI loop is run with a fake ActionRow to avoid requiring a
display server; no private catalogue data is used in fixtures.
"""
import ast
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


def _holdings_render_loop():
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    loops = [
        node for node in ast.walk(module)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Attribute)
        and node.iter.attr == "holdings"
        and isinstance(node.target, ast.Name)
        and node.target.id == "holding"
    ]
    assert len(loops) == 1
    loop = loops[0]
    # Execute the actual production rendering path up to set_subtitle, before
    # suffix construction (which does not affect text/markup semantics).
    rendered = []
    for statement in loop.body:
        rendered.append(statement)
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "set_subtitle"
        ):
            break
    else:
        raise AssertionError("holdings subtitle assignment missing")
    loop.body = rendered
    return compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])),
                   str(SOURCE), "exec")


class _Row:
    def __init__(self):
        self.events = []
        self.markup = True
        self.title = None
        self.subtitle = None

    def set_use_markup(self, enabled):
        self.events.append("set_use_markup")
        self.markup = enabled

    def set_title(self, title):
        self.events.append("set_title")
        self.title = title

    def set_subtitle(self, subtitle):
        self.events.append("set_subtitle")
        self.subtitle = subtitle


def test_holdings_provider_text_is_never_pango_markup():
    values = (
        "Pensée & Spiritualité",
        "A < B > C",
        "&amp;",
        "Littérature — 日本語 📚",
        "",
    )
    rows = []
    class Adw:
        @staticmethod
        def ActionRow():
            row = _Row()
            rows.append(row)
            return row

    holdings = [
        SimpleNamespace(
            library=f"Bibliothèque {value}", location=None,
            status_label="En rayon", status="available",
            section=value, call_number=f"PHILO / {value}" if value else "",
            due_at=None,
        )
        for value in values
    ]
    exec(_holdings_render_loop(), {
        "Adw": Adw,
        "detail": SimpleNamespace(holdings=holdings),
        "fr_date": lambda value: value,
    })
    assert len(rows) == len(holdings)
    for row, holding in zip(rows, holdings):
        assert row.events[:3] == ["set_use_markup", "set_title", "set_subtitle"]
        assert row.markup is False
        assert row.title == holding.library
        expected = ["En rayon"]
        if holding.section:
            expected.append(holding.section)
        if holding.call_number:
            expected.append(f"Cote {holding.call_number}")
        assert row.subtitle == " · ".join(expected)
    assert rows[0].subtitle == (
        "En rayon · Pensée & Spiritualité · Cote PHILO / Pensée & Spiritualité"
    )
