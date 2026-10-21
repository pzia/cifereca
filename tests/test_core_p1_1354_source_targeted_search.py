"""P1 #1354: strict provider-scoped, local-only navigation contract."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.query_model import QueryState

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/cifereca/gui/search_controller.py"


def controller():
    # Compile only the controller's pure navigation function; do not launch
    # a GTK application or query a real provider in CI.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    method = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "search_books")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"__package__": "cifereca.gui",
                 "__name__": "cifereca.gui.search_controller"}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["search_books"]


def host():
    calls = []
    controls = NS(set_state=lambda state, **kw: calls.append(("state", state, kw)))
    service = NS(current_identity=NS(identity_id="alice"),
                 searchable_providers=lambda: [
                     ("kobo", "Kobo"), ("paris", "Paris"),
                 ])
    shell = NS(_closed=False, service=service, search_controls=controls,
               navigate=lambda route: calls.append(("navigate", route)),
               run_search=lambda online: calls.append(("run", online)))
    return shell, calls


def test_valid_single_provider_is_local_only_and_resets_old_filters():
    shell, calls = host()
    assert controller()(shell, "9780306406157", field="isbn",
                        online=False, identity_id="alice",
                        providers=("kobo",))
    state = calls[0][1]
    assert isinstance(state, QueryState)
    assert (state.text, state.field, state.scope, state.providers,
            state.types, state.languages) == (
                "9780306406157", "isbn", "all", ("kobo",), (), (),
            )
    assert calls[1:] == [("navigate", "search"), ("run", False)]


def test_provider_absent_disabled_or_wrong_identity_never_navigates():
    shell, calls = host()
    for providers, identity in [
        (("disabled",), "alice"),
        (("kobo",), "bob"),
        (("kobo", "kobo"), "alice"),
        (("local",), "alice"),
        (("kobo", 17), "alice"),
        (["kobo"], "alice"),
    ]:
        assert controller()(shell, "9780306406157", field="isbn",
                            online=False, identity_id=identity,
                            providers=providers) is False
    assert calls == []
    shell._closed = True
    assert controller()(shell, "9780306406157", providers=("kobo",)) is False
    assert calls == []


def test_no_provider_parameter_preserves_existing_generic_local_search():
    shell, calls = host()
    assert controller()(shell, "Une édition", field="title",
                        identity_id="alice") is True
    assert calls[0][1].providers == ()
    assert calls[-1] == ("run", False)
