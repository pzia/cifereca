"""P1 #1867 — a Kobo catalogue claim must never trigger a false Save action.

These tests use a real SQLite entitlement projection and fake already-connected
capabilities. No Vault/HTTP, Adept, acquisition or implicit download is run.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.core.kobo_manifest_rights import mirror_kobo_manifest_rights
from cifereca.gui.query_model import result_row
from cifereca.services import LibraryService
from tests.test_core_1337_kobo_personal_manifest_rights import _row, _service
from tests.test_search_results_view import _host


def _offer(product="active", credential="vault-alice", **changes):
    return replace(_row(product=product, credential=credential),
                   downloadable=True, **changes)


def _install_fake_vault(service, monkeypatch):
    # Already available runtime interface, no actual source or DRM access.
    lookups = Mock(return_value=NS(resolve_acquisition=lambda *_a: None))
    runtime = Mock(return_value={
        "available": True, "compatible": True, "operational": True,
    })
    monkeypatch.setattr(service, "_session_for_source", lookups)
    monkeypatch.setattr(service, "kobo_adept_state", runtime)
    return lookups, runtime


def test_public_kobo_owned_flag_cannot_advertise_backup_without_manifest(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        lookups, runtime = _install_fake_vault(service, monkeypatch)
        catalogue = _offer()
        assert result_row(catalogue)["state"] == "remote_catalog"
        assert LibraryService.can_direct_download(service, catalogue) is False
        assert service.kobo_personal_right_states("alice") == {}
        lookups.assert_not_called()
        runtime.assert_not_called()
    finally:
        service.close()


def test_kobo_offers_require_active_right_exact_profile_credential_product_and_flag(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        lookups, runtime = _install_fake_vault(service, monkeypatch)
        active = _offer()
        archived = _offer(product="archived")
        assert mirror_kobo_manifest_rights(
            service, [active, _row(product="archived", archived=True)],
            identity_id="alice", credential_id="vault-alice", account_id="account",
        ) == 2
        assert LibraryService.can_direct_download(service, active) is True
        lookups.assert_called_once_with("kobo", "vault-alice")
        runtime.assert_called_once_with()
        lookups.reset_mock()
        runtime.reset_mock()
        # Same title, different credential/product/account/status never inherits
        # the purchased right. A typed boolean and a verified download offer
        # remain independently necessary.
        for invalid in (
            replace(active, source_credential="other-account"),
            replace(active, source_credential="__public__"),
            replace(active, source_credential=""),
            replace(active, record_id="different"),
            replace(active, record_id=""),
            replace(active, owned=False),
            replace(active, owned=1),
            replace(active, downloadable=False),
            archived,
        ):
            assert LibraryService.can_direct_download(service, invalid) is False, invalid
        lookups.assert_not_called()
        runtime.assert_not_called()
        service.select_identity("bob")
        assert LibraryService.can_direct_download(service, active) is False
        lookups.assert_not_called()
        runtime.assert_not_called()
    finally:
        service.close()


def test_stale_kobo_notice_is_never_promoted_by_existing_right(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        lookups, runtime = _install_fake_vault(service, monkeypatch)
        active = _offer()
        mirror_kobo_manifest_rights(
            service, [active], identity_id="alice",
            credential_id="vault-alice", account_id="account",
        )
        stale = replace(active, metadata={
            "search_provenance": "known_catalog_stale",
        })
        assert LibraryService.can_direct_download(service, stale) is False
        lookups.assert_not_called()
        runtime.assert_not_called()
    finally:
        service.close()


def test_kobo_remote_results_explain_catalogue_vs_purchase_and_archived_without_save():
    host = _host()
    rows = [_offer("catalogue"), _offer("active"), _offer("archived")]
    host.service.kobo_personal_right_states = Mock(return_value={
        ("vault-alice", "active"): "active",
        ("vault-alice", "archived"): "archived",
    })
    host.service.can_direct_download = Mock(return_value=True)
    host._remote_search_results = rows
    host._populate_search(rows, announce=False)
    shown = {row.key.split(":")[-1]: row for row in host.search_page.show_rows.call_args.args[0]}
    assert "Catalogue Kobo · aucun achat attesté" in shown["catalogue"].meta
    assert shown["catalogue"].options["action_label"] is None
    assert "Possédé sur Kobo · Mes achats" in shown["active"].meta
    assert shown["active"].options["action_label"] == "Sauvegarder"
    assert "Archivé sur Kobo · droit non actif" in shown["archived"].meta
    assert shown["archived"].options["action_label"] is None
    host.service.can_direct_download.assert_called_once_with(rows[1])
    host.service.kobo_personal_right_states.assert_called_once_with("alice")
    host.download_direct_item.assert_not_called()


def test_kobo_result_does_not_proclaim_download_when_runtime_unavailable():
    host = _host()
    item = _offer()
    host.service.kobo_personal_right_states = Mock(return_value={
        ("vault-alice", "active"): "active",
    })
    host.service.can_direct_download = Mock(return_value=False)
    host._remote_search_results = [item]
    host._populate_search([item], announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert "Possédé sur Kobo" in row.meta
    assert "Sauvegarde Kobo indisponible" in row.meta
    assert row.options["action_label"] is None
    host.service.can_direct_download.assert_called_once_with(item)


def _usage():
    owner = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    fn = next(node for node in ast.parse(owner.read_text()).body
              if isinstance(node, ast.FunctionDef)
              and node.name == "_commercial_search_usage")
    scope = {"result_row": result_row}
    isolated = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        fn,
    ], type_ignores=[]))
    exec(compile(isolated, str(owner), "exec"), scope)
    return scope["_commercial_search_usage"]


def test_kobo_detail_scope_is_ledger_bound_and_read_only():
    usage = _usage()
    item = _offer()
    reader = Mock(return_value={("vault-alice", "active"): "active"})
    service = NS(current_identity=NS(identity_id="alice"),
                 kobo_personal_right_states=reader)
    assert "Mes achats Kobo" in usage(item, service)
    reader.assert_called_once_with("alice")
    reader.reset_mock()
    service.current_identity = NS(identity_id="bob")
    reader.return_value = {}
    assert "Catalogue Kobo · aucun achat attesté" in usage(item, service)
    reader.assert_called_once_with("bob")
    reader.reset_mock()
    reader.return_value = {("vault-alice", "active"): "archived"}
    assert "achat archivé" in usage(item, service)
    reader.reset_mock()
    assert "droits non actualisés" in usage(
        replace(item, metadata={"search_provenance": "known_catalog_stale"}),
        service,
    )
    reader.assert_not_called()
    assert "Catalogue Kobo" in usage(item)  # old lightweight clients


def test_unavailable_scoped_reader_fails_to_catalogue_without_provider_io():
    usage = _usage()
    item = _offer()
    service = NS(current_identity=NS(identity_id="alice"),
                 kobo_personal_right_states=Mock(side_effect=RuntimeError("private")))
    assert "Catalogue Kobo" in usage(item, service)
    assert "private" not in usage(item, service)


def test_kobo_availability_never_promotes_raw_catalogue_owned_flag():
    from cifereca.core.search_orchestration import availability_display_label

    item = _offer()
    yes = Mock(return_value=True)
    service = NS(
        current_identity=NS(identity_id="alice"),
        kobo_personal_right_states=Mock(return_value={}),
        can_direct_download=yes,
    )
    # A non-Kobo provider could publish this label; the Kobo catalogue
    # scope must ignore it and must not invent a loan or a purchase.
    detail = NS(availability_label="Empruntable", availability_status="available")
    assert availability_display_label(service, item, detail) == (
        "Catalogue Kobo · aucun achat attesté"
    )
    yes.assert_not_called()
    service.kobo_personal_right_states.assert_called_once_with("alice")
    assert "aucun achat attesté" in availability_display_label(
        service, replace(item, price="9,99", currency="EUR"), detail,
    )
    yes.assert_not_called()


def test_kobo_availability_confirms_purchase_only_from_core_rights():
    from cifereca.core.search_orchestration import availability_display_label

    item = _offer()
    can_download = Mock(return_value=False)
    reader = Mock(return_value={
        ("vault-alice", "active"): "active",
        ("vault-alice", "archived"): "archived",
    })
    service = NS(
        current_identity=NS(identity_id="alice"),
        kobo_personal_right_states=reader,
        can_direct_download=can_download,
    )
    detail = NS(availability_label="Empruntable", availability_status="available")
    assert availability_display_label(service, item, detail) == (
        "Mes achats Kobo · fichier local non présumé"
    )
    can_download.assert_called_once_with(item)
    can_download.reset_mock()
    assert "Achat Kobo archivé" in availability_display_label(
        service, _offer(product="archived"), detail,
    )
    can_download.assert_not_called()
    can_download.return_value = True
    assert availability_display_label(service, item, detail) == (
        "Mes achats Kobo · sauvegarde possible"
    )
    service.current_identity = NS(identity_id="bob")
    reader.return_value = {}
    can_download.reset_mock()
    assert "Catalogue Kobo" in availability_display_label(service, item, detail)
    can_download.assert_not_called()


def test_kobo_availability_error_is_closed_and_never_shows_loan_or_owned():
    from cifereca.core.search_orchestration import availability_display_label

    service = NS(
        current_identity=NS(identity_id="alice"),
        kobo_personal_right_states=Mock(side_effect=RuntimeError("private")),
        can_direct_download=Mock(side_effect=AssertionError("no right")),
    )
    value = availability_display_label(
        service, _offer(), NS(availability_label="Empruntable"),
    )
    assert "Catalogue Kobo" in value
    assert "achat attesté" in value
    assert "Emprunt" not in value
    assert "private" not in value
