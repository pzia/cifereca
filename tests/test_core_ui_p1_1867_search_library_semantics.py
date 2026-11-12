"""P1 #1867: public notices, personal purchases, loans and local files stay distinct.

All tests are read-only: no provider, GTK, file or acquisition invocation.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.query_model import result_row
from cifereca.core.search_owned_evidence import SearchOwnedEvidence
from tests.test_search_results_view import _book, _host


def _private_numilog():
    return replace(
        _book("numilog"),
        scenario="NUMILOG_LIBRARY",
        source_collection="library",
        source_credential="personal-credential", source_account_id="account-A",
        owned=True,
        downloadable=True,
    )


def _proof():
    return SearchOwnedEvidence(
        "alice", frozenset({("numilog", "personal-credential", "account-A", "same", "")}),
    )


def _right():
    return dict(identity_id="alice", provider_id="numilog",
                credential_id="personal-credential", account_id="account-A",
                item_id="same", revision_id=None, state="active",
                entitlement_type="owned")


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
    assert result_row(_private_numilog())["state"] == "remote_catalog"
    assert result_row(_private_numilog(), purchase_evidence=_proof(),
                      identity_id="alice")["state"] == "remote_owned"


def test_private_numilog_requires_full_scope_and_origin_before_claiming_owned():
    private = _private_numilog()
    variants = (
        replace(private, source_collection="public_catalog"),
        replace(private, source_credential=""),
        replace(private, source_credential="__public__"),
        replace(private, metadata={"search_provenance": "known_catalog_stale"}),
        replace(private, source_provider="numilog-catalog"),
    )
    for variant in variants:
        assert result_row(variant, purchase_evidence=_proof(),
                          identity_id="alice")["state"] == "remote_catalog", repr(variant)
    # Neither supplier scenario nor collection vocabulary creates the right.
    assert result_row(replace(private, scenario="NUMILOG_PUBLIC"),
                      purchase_evidence=_proof(), identity_id="alice")["state"] == "remote_owned"
    for vendor_hint in (None, False, 1):
        actual = result_row(
            replace(private, owned=vendor_hint), purchase_evidence=_proof(),
            identity_id="alice",
        )
        assert actual["state"] == "remote_owned"
        assert actual["owned"] is True


def test_anonymous_numilog_search_shows_notice_not_purchase_or_download():
    host = _host()
    host._remote_search_results = [_public_numilog()]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search(host._remote_search_results, announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert len(rows) == 1
    assert "Catalogue public · aucun achat attesté" in rows[0].meta
    assert "aucun achat attesté" in rows[0].meta
    assert "Mes achats" not in rows[0].meta
    assert rows[0].options["action_label"] is None
    assert rows[0].options["action_callback"] is None
    host.service.can_direct_download.assert_not_called()
    host.download_direct_item.assert_not_called()


def test_private_numilog_inventory_does_not_imply_local_epub():
    host = _host()
    host._remote_search_results = [_private_numilog()]
    host.service.storage = NS(entitlements=Mock(return_value=[_right()]))
    host.service.can_direct_download = Mock(return_value=False)
    host._populate_search(host._remote_search_results, announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert "Mes achats · fichier local non présumé" in row.meta
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
    assert "Mes achats · fichier local non présumé" in usage(
        _private_numilog(), NS(current_identity=NS(identity_id="alice")),
        purchase_evidence=_proof())
    assert "aucun droit d'achat attesté" in usage(
        replace(_private_numilog(), source_collection="library")
    )
    assert usage(replace(_private_numilog(), source_collection="catalog")) == ""
    assert usage(_book("paris")) == ""
    # Kobo retains its supplier-specific *warning* rather than being
    # mislabelled an owned commercial purchase by the shared library scope.
    assert "disponibilité à vérifier" in usage(
        replace(_book("kobo"), source_collection="library", owned=True)
    )
    assert "droits non actualisés" in usage(
        replace(_book("kobo"), source_collection="library",
                metadata={"search_provenance": "known_catalog_stale"})
    )
    # Generic facts, not the provider identifier, govern warning projection.
    for provider_id in ("kobo", "numilog", "synthetic-provider"):
        observed = replace(_book(provider_id), source_collection="library",
                           metadata={"search_provenance": "known_catalog_stale"})
        assert usage(observed) == "Notice connue hors ligne · droits non actualisés"
        assert "disponibilité à vérifier" in usage(
            replace(_book(provider_id), source_collection="library")
        )
    assert usage(replace(_public_numilog(),
                         metadata={"search_provenance": "known_catalog_stale"})) == (
        "Notice connue hors ligne · droits non actualisés"
    )
    # A supplier-name switch must never manufacture purchase provenance.
    synthetic_public = replace(_public_numilog(), source_provider="synthetic-public")
    assert usage(synthetic_public) == "Catalogue commercial public · aucun achat attesté"
    synthetic_private = replace(_private_numilog(), source_provider="synthetic-provider")
    assert "aucun droit d'achat attesté" in usage(synthetic_private)
    synthetic_proof = SearchOwnedEvidence(
        "alice", frozenset({("synthetic-provider", "personal-credential", "account-A", "same", "")}),
    )
    assert usage(
        synthetic_private, NS(current_identity=NS(identity_id="alice")),
        purchase_evidence=synthetic_proof,
    ) == "Mes achats · fichier local non présumé"
    detail_source = owner.read_text(encoding="utf-8").split("class DetailPanel", 1)[0]
    assert 'item.source_provider == "kobo"' not in detail_source
    assert 'item.source_provider == "numilog"' not in detail_source
    assert 'item.source_provider == "numilog-catalog"' not in detail_source


def test_library_references_do_not_confuse_loan_purchase_and_catalogue():
    from tests.test_core_ui_346_local_dedupe import _standalone_pair

    host = _standalone_pair()
    assert host._library_state_label(
        {"state": "remote_owned", "provider_id": "kobo"}
    ) == host._library_state_label(
        {"state": "remote_owned", "provider_id": "numilog"}
    ) == host._library_state_label(
        {"state": "remote_owned", "provider_id": "fictional-provider"}
    ) == "Droit de possession · en ligne"
    assert host._library_state_label(
        {"state": "remote_archived", "provider_id": "fictional-provider"}
    ) == "Droit archivé · en ligne"
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


def test_public_source_label_is_neutral_for_a_future_provider_without_rights():
    host = _host()
    row = replace(_public_numilog(), source_provider="synthetic-public")
    host._remote_search_results = [row]
    host.service.can_direct_download = Mock(return_value=False)
    host._populate_search([row], announce=False)
    result = host.search_page.show_rows.call_args.args[0][0]
    assert "Catalogue public · aucun achat attesté" in result.meta
    assert "Mes achats" not in result.meta
    assert result.options["action_label"] is None
    host.download_direct_item.assert_not_called()


def test_p0_5056_search_labels_do_not_dispatch_by_numilog_identity():
    source = (Path(__file__).resolve().parents[1]
              / "src/cifereca/gui/search_results.py").read_text(encoding="utf-8")
    assert 'if item.source_provider == "numilog-catalog":' not in source
    assert 'elif item.source_provider == "numilog"' not in source
    assert "Catalogue public · aucun achat attesté" in source
    assert "Mes achats · fichier local non présumé" in source
