"""P1 #1867: public notices, personal purchases, loans and local files stay distinct.

All tests are read-only: no provider, GTK, file or acquisition invocation.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from cifereca.gui.query_model import result_row
from tests.test_search_results_view import _book, _host


def _private_numilog():
    return replace(
        _book("numilog"),
        scenario="NUMILOG_LIBRARY",
        source_collection="library",
        source_credential="personal-credential",
        owned=True,
        downloadable=True,
    )


def _public_numilog():
    return replace(
        _book("numilog-catalog"),
        scenario="NUMILOG_PUBLIC",
        source_collection="public_catalog",
        source_credential="__public__",
        owned=True,  # deliberately malicious/contradictory provider claim
        downloadable=True,
    )


def test_remote_catalogue_cannot_be_promoted_to_owned_by_search_flags():
    public = _public_numilog()
    assert result_row(public)["state"] == "remote_catalog"
    assert result_row(replace(_book("kobo"), owned=True))["state"] == "remote_catalog"
    assert result_row(replace(_book("paris"), owned=True))["state"] == "remote_catalog"
    assert result_row(_private_numilog())["state"] == "remote_owned"


def test_private_numilog_requires_full_scope_and_origin_before_claiming_owned():
    private = _private_numilog()
    variants = (
        replace(private, owned=1),
        replace(private, owned=False),
        replace(private, scenario="NUMILOG_PUBLIC"),
        replace(private, source_collection="public_catalog"),
        replace(private, source_collection=None),
        replace(private, source_credential=""),
        replace(private, source_credential="__public__"),
        replace(private, metadata={"search_provenance": "known_catalog_stale"}),
        replace(private, source_provider="numilog-catalog"),
    )
    for variant in variants:
        assert result_row(variant)["state"] == "remote_catalog", repr(variant)


def test_anonymous_numilog_search_shows_notice_not_purchase_or_download():
    host = _host()
    host._remote_search_results = [_public_numilog()]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search(host._remote_search_results, announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert len(rows) == 1
    assert "Catalogue public Numilog" in rows[0].meta
    assert "aucun achat attesté" in rows[0].meta
    assert "Mes achats" not in rows[0].meta
    assert rows[0].options["action_label"] is None
    assert rows[0].options["action_callback"] is None
    host.service.can_direct_download.assert_not_called()
    host.download_direct_item.assert_not_called()


def test_private_numilog_inventory_does_not_imply_local_epub():
    host = _host()
    host._remote_search_results = [_private_numilog()]
    host.service.can_direct_download = Mock(return_value=False)
    host._populate_search(host._remote_search_results, announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert "Mes achats Numilog" in row.meta
    assert "fichier local non présumé" in row.meta
    assert "Fichier local disponible" not in row.meta
    assert row.options["action_label"] is None
    assert host.service.local_placements_for_sources.call_count == 1


def test_private_numilog_malformed_origin_is_not_tagged_mes_achats():
    host = _host()
    host._remote_search_results = [
        replace(_private_numilog(), scenario="NUMILOG_PUBLIC"),
    ]
    host._populate_search(host._remote_search_results, announce=False)
    assert "Mes achats" not in host.search_page.show_rows.call_args.args[0][0].meta


def test_detail_usage_is_read_only_and_matches_search_projection():
    owner = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    func = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef)
                and n.name == "_commercial_search_usage")
    scope = {"result_row": result_row}
    isolated = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        func,
    ], type_ignores=[]))
    exec(compile(isolated, str(owner), "exec"), scope)
    usage = scope["_commercial_search_usage"]
    assert "aucun achat attesté" in usage(_public_numilog())
    assert "Mes achats Numilog" in usage(_private_numilog())
    assert "aucun droit d'achat attesté" in usage(
        replace(_private_numilog(), source_collection="catalog")
    )
    assert usage(_book("paris")) == ""


def test_library_references_do_not_confuse_loan_purchase_and_catalogue():
    from tests.test_core_ui_346_local_dedupe import _standalone_pair

    host = _standalone_pair()
    assert "Achat Kobo" in host._library_state_label(
        {"state": "remote_owned", "provider_id": "kobo"}
    )
    assert "Achat Numilog" in host._library_state_label(
        {"state": "remote_owned", "provider_id": "numilog"}
    )
    assert "Notice de catalogue" in host._library_state_label(
        {"state": "remote_catalog", "provider_id": "numilog-catalog"}
    )
    assert "Emprunté" == host._library_state_label(
        {"state": "remote_loan_active", "provider_id": "paris"}
    )
    assert "Local" == host._library_state_label(
        {"state": "local", "provider_id": "numilog"}
    )
    assert "Achat" not in host._library_state_label(
        {"state": "remote_catalog", "provider_id": "numilog-catalog"}
    )
