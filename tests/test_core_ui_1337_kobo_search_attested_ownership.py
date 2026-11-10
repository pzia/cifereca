"""#3196 — Search only presents Kobo origin observations, never pseudo-rights."""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

from tests.test_core_1337_kobo_personal_manifest_rights import _row


def _render_search_results(host):
    """Execute the production view adapter with a no-display GTK stand-in."""
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/search_results.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    # The actual search adapter depends on pure exact-edition helpers from
    # the same production module. Keep them in this AST-isolated headless view
    # rather than reimplementing the result logic in a test stub.
    function_names = (
        "_catalogue_edition_scope",
        "_exact_known_catalogue_scope",
        "_exact_remote_catalogue_scope",
        "_remote_result_row_keys",
        "_render_search_results",
    )
    functions = [
        item for name in function_names for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    ]
    assert len(functions) == len(function_names)
    tree = ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            *functions,
        ],
        type_ignores=[],
    ))
    ns = {
        "BookListItem": lambda *args, **_kw: NS(
            key=args[0], title=args[1], metadata=args[3],
        ),
        "result_row": lambda item, **_kwargs: {"title": item.title},
        "file_format": lambda _value: "epub",
    }
    exec(compile(tree, str(source), "exec"), ns)
    return ns["_render_search_results"](host)


def _fake_host(rows, *, identity_id="alice"):
    seen = []
    service = NS(
        current_identity=NS(identity_id=identity_id),
        local_placements_for_sources=lambda _id: {},
        provider_display_name=lambda _pid: "Kobo",
        content_type_for=lambda _item: "ebook",
        can_direct_download=lambda _item: False,
    )
    host = NS(
        service=service,
        search_page=NS(show_rows=lambda elements: seen.extend(elements)),
        search_controls=NS(state=lambda: NS(filter=lambda elements: list(elements))),
        _search_mode="global",
        _search_local_objects={},
        _remote_search_results=rows,
        _attach_row_facets=lambda *_args: None,
        _update_search_count=lambda _n: None,
    )
    return host, seen



def test_kobo_observations_never_become_owned_or_archived_actions():
    rows = [
        _row(product="active", owned=True),
        _row(product="archived", archived=True),
        _row(product="public", owned=False),
    ]
    host, shown = _fake_host(rows)
    assert _render_search_results(host) == 3
    assert all("Possédé sur Kobo" not in item.metadata for item in shown)
    assert all("droit actif" not in item.metadata for item in shown)
    assert all("disponibilité" not in item.metadata or "vérifier" in item.metadata
               for item in shown)


def test_kobo_observation_does_not_use_other_identity_or_call_download():
    host, shown = _fake_host([_row()], identity_id="bob")
    assert _render_search_results(host) == 1
    assert "Possédé sur Kobo" not in shown[0].metadata
