"""#1337 — Search and Library use the same Kobo ownership evidence."""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.kobo_manifest_rights import mirror_kobo_manifest_rights
from tests.test_core_1337_kobo_personal_manifest_rights import _row, _service


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
        "result_row": lambda item: {"title": item.title},
        "file_format": lambda _value: "epub",
    }
    exec(compile(tree, str(source), "exec"), ns)
    return ns["_render_search_results"](host)


def _fake_host(rows, states, *, identity_id="alice"):
    seen = []
    service = NS(
        current_identity=NS(identity_id=identity_id),
        local_placements_for_sources=lambda _id: {},
        kobo_personal_right_states=lambda _id: states if _id == identity_id else {},
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


def test_search_ownership_and_archive_labels_require_personal_rights(tmp_path):
    service = _service(tmp_path)
    active = _row(product="active-1", credential="vault-alice")
    archived = _row(product="archive-1", credential="vault-alice", archived=True)
    catalog_claim = _row(product="catalog-claim", credential="vault-alice")
    try:
        assert mirror_kobo_manifest_rights(
            service, [active, archived], identity_id="alice",
            credential_id="vault-alice", account_id="personal",
        ) == 2
        states = service.kobo_personal_right_states("alice")
        assert states == {
            ("vault-alice", "active-1"): "active",
            ("vault-alice", "archive-1"): "archived",
        }
        assert service.kobo_personal_right_states("bob") == {}

        # The catalog's raw owned=True claim is insufficient without a
        # manifest entitlement; an owned=False catalog item can nonetheless
        # display the attested purchase.
        host, shown = _fake_host([
            replace(active, owned=False),
            archived,
            catalog_claim,
        ], states)
        assert _render_search_results(host) == 3
        labels = {item.title + ":" + item.key.split(":")[-1]: item.metadata for item in shown}
        assert "Possédé sur Kobo" in labels["Livre acheté:active-1"]
        assert "Archivé sur Kobo" in labels["Livre acheté:archive-1"]
        assert "Possédé sur Kobo" not in labels["Livre acheté:catalog-claim"]
        assert "Archivé sur Kobo" not in labels["Livre acheté:catalog-claim"]
    finally:
        service.close()


def test_search_cannot_reuse_other_identity_kobo_rights(tmp_path):
    service = _service(tmp_path)
    purchased = _row(product="alice-private", credential="vault-alice")
    try:
        mirror_kobo_manifest_rights(
            service, [purchased], identity_id="alice",
            credential_id="vault-alice", account_id="personal",
        )
        host, shown = _fake_host(
            [purchased], service.kobo_personal_right_states("bob"),
            identity_id="bob",
        )
        assert _render_search_results(host) == 1
        assert "Possédé sur Kobo" not in shown[0].metadata
    finally:
        service.close()
