from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .core import CiferecaCore
from .install_paths import resolve_archive


def safe_model(obj: Any) -> dict[str, Any]:
    data = asdict(obj)
    data.pop("raw", None)
    data.pop("ebook_url", None)
    return data


def _safe_kobo_resume_receipt(summary: dict[str, Any]) -> dict[str, Any]:
    """Return only bounded, non-identifying counters for one explicit run.

    The engine summary may include local paths, book titles, raw credentials,
    error strings and provider metadata. None belongs in CLI output.
    """
    states = {"complete", "partial", "failed", "aborted", "cancelled", "attention_required"}
    state = str(summary.get("status") or "")
    result: dict[str, Any] = {
        "scope": "account", "status": state if state in states else "unknown",
        "resume_attempted": True,
    }
    for key in (
        "manifest_items", "scanned", "attempted", "downloaded", "unchanged",
        "completed", "pending", "attention_required", "skipped_archived", "errors", "remaining",
        "reconcile_pending", "reconcile_skipped", "blocked_non_retryable",
        "items_blocked_by_scope", "malformed_manifest_items",
        "duplicate_manifest_items", "provider_rejected_manifest_items",
    ):
        value = summary.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            result[key] = value
    return result


def _safe_kobo_progress_count(value: Any) -> int:
    """Render only a bounded, non-identifying Kobo progress counter."""
    return value if type(value) is int and 0 <= value <= 1_000_000 else 0


def _safe_kobo_sync_receipt(summary: dict[str, Any]) -> dict[str, Any]:
    """Copyable Kobo sync output without private per-book or provider data."""
    from .core.kobo_backup import _safe_kobo_backup_diagnostic_detail

    # This serializer is also used by Diagnostics.add; it rejects all free
    # text, account identifiers, titles, signed URLs, local paths and tokens.
    return {"redacted": True, **_safe_kobo_backup_diagnostic_detail(summary)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CLI auxiliaire multi-provider cifereca")
    p.add_argument("--version", action="version", version=f"cifereca {__version__}")
    p.add_argument("--username", help="Compatibilité: login Paris ponctuel au lieu des providers configurés")
    p.add_argument("--account", help="ID local du credential provider à sélectionner")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("diagnose")
    doctor = sub.add_parser("doctor", help="Diagnostic technique consolidé, expurgé et copiable")
    doctor.add_argument("--verbose", action="store_true")
    doctor.add_argument("--json", action="store_true")
    sub.add_parser("loans")
    sub.add_parser("history")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--scope", choices=["all", "physical", "digital", "unknown"], default="all")
    s.add_argument("--provider", default="all", help="Provider id (paris, gallica, kobo, ...)")
    s.add_argument("--field", choices=["all", "title", "author", "isbn", "subject"], default="all")
    generic_sync = sub.add_parser("sync", help="Synchronisation ciblée par source, indépendante du fournisseur")
    generic_sub = generic_sync.add_subparsers(dest="sync_cmd", required=True)
    for command in ("list", "run"):
        command_parser = generic_sub.add_parser(command)
        command_parser.add_argument("--provider", default="")
        command_parser.add_argument("--identity", default="")
        command_parser.add_argument("--credential", default="")
        command_parser.add_argument("--account-id", default=None)
        command_parser.add_argument("--json", action="store_true")
    generic_sub.choices["run"].add_argument(
        "--confirm", action="store_true", help="Autorise explicitement les opérations réseau de cette source"
    )
    generic_sub.choices["run"].add_argument(
        "--trace-export", type=Path, metavar="ZIP",
        help="Capture HTTP brute privée 0600 pendant la sync ciblée ; peut contenir des credentials"
    )
    generic_sub.choices["run"].add_argument(
        "--confirm-trace-export", action="store_true",
        help="Je confirme la création locale d'un ZIP HTTP brut incluant possiblement des secrets"
    )
    ks_sync = sub.add_parser("kobo-sync", help="Synchronise localement les ebooks Kobo possédés")
    ks_sync.add_argument("--quiet", action="store_true", help="N’affiche pas la progression sur stderr")
    kb = sub.add_parser("kobo-backup", help="Alias historique de kobo-sync")
    kb.add_argument("--quiet", action="store_true", help="N’affiche pas la progression sur stderr")
    sub.add_parser("kobo-sync-errors", help="Diagnostic local de la synchronisation Kobo")
    sub.add_parser("kobo-backup-errors", help="Alias historique de kobo-sync-errors")
    kobo_audit = sub.add_parser(
        "kobo-audit", help="Audit Kobo local SHA-256, lecture seule, JSON expurgé"
    )
    kobo_audit.add_argument(
        "--credential", help="Limiter l'audit à un compte Kobo (non affiché dans le rapport)"
    )
    kobo_audit.add_argument(
        "--plan", action="store_true",
        help="Joindre un plan de vérifications manuelles, sans modifier ni télécharger de fichiers",
    )
    reconcile = sub.add_parser(
        "kobo-reconcile-local", help="Rapprochement Kobo local confirmé, atomique et réversible"
    )
    reconcile.add_argument("--credential", required=True)
    reconcile.add_argument("--identity", help="Refuser une connexion liée à une autre identité")
    reconcile.add_argument("--account-id", required=True)
    reconcile.add_argument("--item", required=True)
    reconcile.add_argument("--sha256", required=True, help="SHA déjà attesté dans le ledger, jamais inféré du titre")
    reconcile.add_argument("--path", help="Fichier EPUB/PDF dans le dossier de cette identité")
    reconcile.add_argument("--undo", help="Identifiant opaque du rapprochement à annuler (aperçu par défaut)")
    reconcile.add_argument("--confirm", help="Token de l’aperçu exact ; son absence interdit toute écriture")
    kobo_preview = sub.add_parser(
        "kobo-resume-preview",
        help="Aperçu en lecture seule des candidats Kobo (aucun téléchargement)",
    )
    kobo_preview.add_argument("--credential", required=True, help="Identifiant de connexion Kobo")
    kobo_preview.add_argument(
        "--refresh-manifest", action="store_true",
        help="Lire seulement le manifeste distant (sans lien d'acquisition ni téléchargement)",
    )
    kobo_preview.add_argument(
        "--verify-files", action="store_true",
        help="Inspecter et hacher les fichiers liés (peut prendre du temps)",
    )
    kobo_resume = sub.add_parser(
        "kobo-resume",
        help="Reprise manuelle ciblée Kobo : peut télécharger des livres (confirmation requise)",
    )
    kobo_resume.add_argument("--credential", required=True, help="Identifiant de connexion Kobo")
    kobo_resume.add_argument(
        "--confirm-downloads", action="store_true",
        help="J'autorise explicitement la reprise et les téléchargements de ce compte",
    )
    kobo_resume.add_argument(
        "--retry-scope-blocked", action="store_true",
        help="Réessayer les refus de délégation 403 après réautorisation Kobo-Vault",
    )
    kobo_resume.add_argument(
        "--confirm-reauthorized", action="store_true",
        help="Je confirme avoir réautorisé/reconnecté ce compte avant le réessai de scope",
    )
    directory = sub.add_parser("identity-directory", help="Change le dossier d’une identité avec fusion et rescan")
    directory.add_argument("identity_id", nargs="?")
    directory.add_argument("directory_name", nargs="?")
    directory.add_argument("--dry-run", action="store_true")
    directory.add_argument("--abort", action="store_true", help="Annule une migration avant validation en base ; originaux conservés")
    scan=sub.add_parser("catalog-scan", help="Scanne ~/EBooks et réconcilie le catalogue local")
    scan.add_argument("--refresh-covers", action="store_true", help="Réinspecte les couvertures EPUB, sans téléchargement")
    stats=sub.add_parser("statistics", help="Statistiques locales et prêts connus, sans connexion")
    stats.add_argument("--identity", help="Identité à décrire ; toutes par défaut")
    cl = sub.add_parser("catalog-list", help="Liste le catalogue local 0.8")
    cl.add_argument("--state", choices=["local", "missing"], default=None)
    al = sub.add_parser("acquisition-ledger", help="Liste le ledger générique d'acquisition")
    al.add_argument("--status", choices=["discovered", "pending", "acquiring", "complete", "retryable_error", "attention_required"], default=None)
    sub.add_parser("acquisition-reconcile", help="Réconcilie acquisitions interrompues, prêts expirés et catalogue local")
    paris_adept = sub.add_parser(
        "paris-adept", help="Liaisons exceptionnelles explicites des comptes Paris et profils ADEPT"
    )
    adept_sub = paris_adept.add_subparsers(dest="paris_adept_cmd", required=True)
    adept_list = adept_sub.add_parser("list", help="Liste les liaisons ADEPT locales confirmées (hors réseau)")
    adept_list.add_argument("--credential", help="Limiter à une connexion Paris")
    adept_sub.add_parser("accounts", help="Affiche les comptes visibles après connexion Paris")
    adept_audit = adept_sub.add_parser(
        "audit", help="Audit local hors réseau des prêts Paris visibles sur plusieurs connexions"
    )
    adept_audit.add_argument(
        "--credential", help="Filtre les prêts liés à cette connexion, sans masquer les autres provenances"
    )
    adept_exact = adept_sub.add_parser(
        "audit-origin",
        help="Audit historique ADEPT hors réseau sur un prêt Paris et un item exacts",
    )
    adept_exact.add_argument("--loan-id", required=True, help="ID exact de prêt Paris")
    adept_exact.add_argument("--item-id", required=True, help="ID exact de l'item du prêt")
    adept_canonical = adept_sub.add_parser(
        "audit-canonical",
        help="Revue locale v14 des liens fournisseur/ADEPT d'un prêt Paris exact",
    )
    adept_canonical.add_argument("--loan-id", required=True, help="ID exact du prêt Paris")
    adept_canonical.add_argument("--item-id", required=True, help="ID exact de l'item")
    adept_bridge = adept_sub.add_parser(
        "audit-transaction",
        help="Compare hors réseau les preuves Paris/ADEPT historiques et canoniques",
    )
    adept_bridge.add_argument("--loan-id", required=True, help="ID exact du prêt Paris")
    adept_bridge.add_argument("--item-id", required=True, help="ID exact de l'item")
    adept_preview = adept_sub.add_parser(
        "preview-resolution",
        help="Prévisualisation sans mutation d'une origine ADEPT explicitement sélectionnée",
    )
    adept_preview.add_argument("--loan-id", required=True)
    adept_preview.add_argument("--item-id", required=True)
    adept_preview.add_argument("--source-reference-id", required=True)
    adept_preview.add_argument("--acquisition-ref", required=True)
    adept_bind = adept_sub.add_parser("bind", help="Confirme une exception de profil ADEPT pour un compte Paris")
    adept_bind.add_argument("--credential", required=True, help="ID exact de la connexion Paris")
    adept_bind.add_argument("--borrower", required=True, help="Compte Paris : primary ou identifiant exact du sous-compte")
    adept_bind.add_argument("--identity", required=True, help="Identité portant le profil ADEPT effectif attesté")
    adept_bind.add_argument(
        "--confirm-ownership", action="store_true",
        help="Atteste une vérification humaine du vrai propriétaire Adobe du prêt",
    )
    adept_unbind = adept_sub.add_parser("unbind", help="Révoque la liaison ADEPT enregistrée")
    adept_unbind.add_argument("--credential", required=True)
    adept_unbind.add_argument("--borrower", required=True)

    plugin = sub.add_parser("plugin", help="Gère les plugins cifereca")
    psub = plugin.add_subparsers(dest="plugin_cmd", required=True)
    psub.add_parser("list", help="Liste les plugins")
    inspect = psub.add_parser("inspect", help="Décrit un plugin installé"); inspect.add_argument("plugin_id")
    install = psub.add_parser("install", help="Installe un ZIP de plugin"); install.add_argument("archive", type=Path); install.add_argument("--no-activate", action="store_true"); install.add_argument("--replace", action="store_true", help=argparse.SUPPRESS)
    enable = psub.add_parser("enable"); enable.add_argument("plugin_id")
    disable = psub.add_parser("disable"); disable.add_argument("plugin_id")
    remove = psub.add_parser("remove", help="Désinstalle un package"); remove.add_argument("package_id"); remove.add_argument("--version")
    rollback = psub.add_parser("rollback"); rollback.add_argument("package_id")
    diagnose_plugin = psub.add_parser("diagnose"); diagnose_plugin.add_argument("plugin_id", nargs="?")
    priority = psub.add_parser("priority"); priority.add_argument("plugin_id"); priority.add_argument("value", type=int)
    config_get = psub.add_parser("config-get"); config_get.add_argument("plugin_id"); config_get.add_argument("--scope-id")
    config_set = psub.add_parser("config-set"); config_set.add_argument("plugin_id"); config_set.add_argument("json_values"); config_set.add_argument("--scope-id")
    packages = psub.add_parser("packages", help="Liste les packages/versions installés")
    deps_repair = psub.add_parser("deps-repair", help="Résout les dépendances Python des plugins actifs dans le venv Core")
    a = p.parse_args(argv)
    if a.cmd == "kobo-resume":
        # Fail before even opening Core: an accidental invocation must not
        # contact a provider, modify a database or initiate any transfer.
        if not a.confirm_downloads:
            p.error("kobo-resume exige --confirm-downloads avant tout transfert")
        if a.retry_scope_blocked and not a.confirm_reauthorized:
            p.error("--retry-scope-blocked exige --confirm-reauthorized après réautorisation")
        if a.confirm_reauthorized and not a.retry_scope_blocked:
            p.error("--confirm-reauthorized exige --retry-scope-blocked")
    if a.cmd == "paris-adept":
        if a.username or a.account:
            p.error("paris-adept exige --credential, pas --username ou --account")
        if a.paris_adept_cmd in {
            "audit-origin", "audit-canonical", "audit-transaction", "preview-resolution",
        }:
            # Reject unusable transaction keys before Core can be opened.
            if (not a.loan_id.strip() or not a.item_id.strip()
                    or "\x00" in a.loan_id or "\x00" in a.item_id):
                p.error("audit de transaction exige --loan-id et --item-id non vides et sans NUL")
        if a.paris_adept_cmd == "preview-resolution":
            if (a.loan_id != a.loan_id.strip() or a.item_id != a.item_id.strip()
                    or "\r" in a.loan_id or "\n" in a.loan_id
                    or "\r" in a.item_id or "\n" in a.item_id
                    or not a.source_reference_id.strip()
                    or a.source_reference_id != a.source_reference_id.strip()
                    or "\x00" in a.source_reference_id
                    or "\r" in a.source_reference_id or "\n" in a.source_reference_id
                    or len(a.acquisition_ref) != 16
                    or any(char not in "0123456789abcdef"
                           for char in a.acquisition_ref)):
                p.error("preview-resolution exige des références exactes et opaques")
        if a.paris_adept_cmd == "bind" and not a.confirm_ownership:
            p.error(
                "paris-adept bind exige --confirm-ownership : "
                "vérifiez explicitement l'emprunteur et le profil Adobe avant validation"
            )

    if a.cmd == "identity-directory" and a.abort:
        try:
            result = CiferecaCore.abort_directory_migration()
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        except Exception:
            # Filesystem/database failures may include the user's library path,
            # identity directory or provider secrets. Never print raw text.
            print("Annulation de migration impossible", file=sys.stderr)
            return 1

    # A local repair/preview must not activate plugins, chmod Config, purge
    # caches or recover a directory migration as a side effect of startup.
    if a.cmd == "kobo-reconcile-local":
        from .core.kobo_local_reconciliation import ReconciliationRejected, reconcile_existing_profile
        try:
            if a.account and a.account != a.credential:
                raise ReconciliationRejected("source_scope_mismatch")
            result = reconcile_existing_profile(
                credential_id=a.credential, account_id=a.account_id, item_id=a.item,
                expected_sha256=a.sha256, path=a.path, confirmation=a.confirm,
                undo_operation=a.undo, identity_id=a.identity,
            )
        except Exception as exc:
            result = {"ok": False, "offline": True, "changed": 0,
                      "reason": exc.code if isinstance(exc, ReconciliationRejected) else "reconciliation_unavailable"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2

    if a.cmd == "sync" and a.sync_cmd == "run":
        from .core.sync_cli_contract import SyncSelectionError
        # Reject unsafe/incomplete requests before opening Core, loading
        # plugins or creating a provider session.
        preflight_code = None
        if a.username or a.account:
            preflight_code = "legacy_account_selector_unsupported"
        elif not a.confirm:
            preflight_code = "explicit_run_confirmation_required"
        elif not a.identity:
            preflight_code = "source_scope_required"
        elif a.trace_export is not None and not a.confirm_trace_export:
            preflight_code = "trace_export_confirmation_required"
        elif a.confirm_trace_export and a.trace_export is None:
            preflight_code = "trace_export_destination_required"
        elif a.trace_export is not None and os.path.lexists(a.trace_export):
            # Refuse before any remote provider connection or acquisition.
            # Core export also checks O_EXCL against symlink/concurrent races.
            preflight_code = "trace_export_destination_exists"
        if preflight_code is not None:
            print(json.dumps({"status": "failed", "code": preflight_code},
                             ensure_ascii=False))
            return 2

    core = CiferecaCore.open(diagnostics_enabled=True, diagnostics_level="detailed")
    service = core.library
    try:
        if a.cmd == "sync":
            from .core.sync_cli_contract import (
                SyncSelectionError, select_sync_accounts, require_exact_sync_account,
                require_unique_sync_account, safe_sync_result,
            )
            # Initial connection can authenticate unrelated configured sources.
            # Never enable raw HTTP capture until the exact target is selected.
            trace_requested = a.sync_cmd == "run" and a.trace_export is not None
            trace_started = False
            trace_outcome = "not_started"
            try:
                if a.username or a.account:
                    raise SyncSelectionError("legacy_account_selector_unsupported")
                # Fail before provider discovery: connect_configured may
                # perform authentication/network I/O, even when no book sync
                # has been requested yet.
                if a.sync_cmd == "run" and not a.confirm:
                    raise SyncSelectionError("explicit_run_confirmation_required")
                # list reads persisted bindings only: no connect, no
                # provider session and no remote inventory request.
                selectors = dict(
                    provider=a.provider, identity=a.identity,
                    credential=a.credential, account_id=a.account_id,
                )
                if a.sync_cmd == "run":
                    # Resolve one exact enabled persisted binding before ANY
                    # connection/authentication. Ambiguity must not authorize
                    # a provider network call or accidental cross-account run.
                    from types import SimpleNamespace
                    connections = {
                        (row["identity_id"], row["provider_id"], row["credential_id"])
                        for row in core.storage.provider_connections()
                        if row["enabled"] is True
                    }
                    eligible = [
                        SimpleNamespace(
                            identity_id=row["identity_id"],
                            provider_id=row["provider_id"],
                            credential_id=row["credential_id"],
                            unique_id=row["account_id"],
                        )
                        for row in core.storage.provider_accounts()
                        if (row["identity_id"], row["provider_id"], row["credential_id"])
                        in connections
                    ]
                    resolved = require_unique_sync_account(eligible, **selectors)
                    selectors = dict(
                        provider=resolved.provider_id,
                        identity=resolved.identity_id,
                        credential=resolved.credential_id,
                        account_id=resolved.unique_id,
                    )
                    service.connect_selected_source(
                        identity=resolved.identity_id,
                        provider=resolved.provider_id,
                        credential=resolved.credential_id,
                    )
                if a.sync_cmd == "list":
                    from types import SimpleNamespace
                    connections = {
                        (row["identity_id"], row["provider_id"], row["credential_id"]): row
                        for row in core.storage.provider_connections()
                    }
                    persisted = [
                        SimpleNamespace(
                            provider_id=row["provider_id"],
                            identity_id=row["identity_id"],
                            credential_id=row["credential_id"],
                            unique_id=row["account_id"],
                        )
                        for row in core.storage.provider_accounts()
                        if (row["identity_id"], row["provider_id"], row["credential_id"])
                        in connections
                    ]
                    selected = select_sync_accounts(persisted, **selectors)
                    from .core.source_sync_consent import read_policy, scope_for_account
                    result = {"status": "success", "sources": [
                        {"provider": x.provider_id, "identity": x.identity_id,
                         "credential": x.credential_id, "account_id": x.unique_id,
                         "connection_enabled": bool(connections[(x.identity_id, x.provider_id, x.credential_id)]["enabled"]),
                         "acquisition_consent": read_policy(
                             core.storage, scope_for_account(x)
                         ).mode}
                        for x in selected
                    ]}
                    code = 0
                else:
                    if not a.confirm:
                        raise SyncSelectionError("explicit_run_confirmation_required")
                    account = require_exact_sync_account(list(service.accounts), **selectors)
                    if trace_requested:
                        from .logging_utils import diagnostics_level, set_diagnostics_level
                        trace_previous_level = diagnostics_level()
                        set_diagnostics_level("trace")
                        trace_started = True
                    raw = service.synchronize_account_source(account)
                    result = {"provider": account.provider_id,
                              "identity": account.identity_id,
                              **safe_sync_result(raw)}
                    code = 0 if result["status"] in ("success", "complete") else 2
            except SyncSelectionError as exc:
                result = {"status": "failed", "code": str(exc)}
                code = 2
            except Exception:
                result = {"status": "failed", "code": "sync_unavailable"}
                code = 2
            finally:
                if trace_started:
                    try:
                        from .http_trace import HTTP_TRACE
                        # O_EXCL, atomic ZIP and 0600 are enforced by Core.
                        # Capture is opt-in and never uploaded or echoed.
                        HTTP_TRACE.export(a.trace_export)
                        trace_outcome = "created"
                    except Exception:
                        trace_outcome = "export_failed"
                    finally:
                        set_diagnostics_level(trace_previous_level)
            if trace_requested:
                result["http_trace"] = trace_outcome
                if trace_outcome == "export_failed":
                    code = 2
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return code
        if a.cmd == "doctor":
            from .support_diagnostic import build_support_report, support_report_text
            if a.json:
                print(json.dumps(build_support_report(service, verbose=a.verbose), ensure_ascii=False, indent=2))
            else:
                print(support_report_text(service, verbose=a.verbose), end="")
            return 0
        if a.cmd == "identity-directory":
            if not a.identity_id or not a.directory_name:
                p.error("identity-directory exige identity_id et directory_name, ou --abort")
            result = service.change_identity_directory(a.identity_id, a.directory_name, dry_run=a.dry_run)
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        if a.cmd == "paris-adept":
            try:
                if a.paris_adept_cmd == "accounts":
                    service.connect_configured()
                    result = [
                        {
                            "credential_id": account.credential_id,
                            "account_id": "primary" if account.is_primary else account.unique_id,
                            "display_name": account.display_name,
                            "connection_identity_id": account.identity_id,
                            "is_primary": bool(account.is_primary),
                        }
                        for account in service.accounts
                        if str(account.provider_id).lower() == "paris"
                    ]
                elif a.paris_adept_cmd == "audit":
                    from .paris_adept_audit import build_paris_adept_audit
                    result = build_paris_adept_audit(
                        core.storage, credential_id=a.credential
                    )
                elif a.paris_adept_cmd == "audit-origin":
                    from .artifact_inspection import inspect_artifact
                    from .core.adept_origin_audit import inspect_paris_adept_origins
                    # Exact loan + item. This does not connect Paris, inspect
                    # remote Adobe inventories or authorize a return. Evidence
                    # from other local identities is deliberately retained.
                    result = inspect_paris_adept_origins(
                        loan_id=a.loan_id.strip(), item_id=a.item_id.strip(),
                        entitlements=core.storage.entitlements(provider_id="paris"),
                        acquisitions=core.storage.acquisitions(provider_id="paris"),
                        artifact_inspector=inspect_artifact,
                    )
                elif a.paris_adept_cmd == "audit-canonical":
                    from .core.adept_canonical_origin_review import (
                        review_paris_canonical_adept_links,
                    )
                    # SELECT-only canonical v14: no profile inference from a
                    # common SHA or destination library, no server side effects.
                    result = review_paris_canonical_adept_links(
                        core.storage, loan_id=a.loan_id.strip(),
                        item_id=a.item_id.strip(),
                    )
                elif a.paris_adept_cmd == "audit-transaction":
                    from .artifact_inspection import inspect_artifact
                    from .core.adept_transaction_reconciliation import (
                        review_paris_adept_transaction,
                    )
                    # Local reports only: an overlap is never an Adobe return
                    # permission or a proof of the activating Adobe profile.
                    result = review_paris_adept_transaction(
                        core.storage, loan_id=a.loan_id.strip(),
                        item_id=a.item_id.strip(),
                        artifact_inspector=inspect_artifact,
                    )
                elif a.paris_adept_cmd == "preview-resolution":
                    from .artifact_inspection import inspect_artifact
                    from .core.adept_resolution_preview import (
                        preview_paris_adept_resolution_candidate,
                    )
                    # Preview only: user-selected refs are not authorization
                    # tokens. No ADEPT return or re-fulfillment is invoked.
                    result = preview_paris_adept_resolution_candidate(
                        core.storage, loan_id=a.loan_id.strip(),
                        item_id=a.item_id.strip(),
                        source_reference_id=a.source_reference_id,
                        acquisition_ref=a.acquisition_ref,
                        artifact_inspector=inspect_artifact,
                    )
                elif a.paris_adept_cmd == "list":
                    bindings = core.storage.paris_borrower_adept_bindings(
                        credential_id=a.credential
                    )
                    for binding in bindings:
                        binding["configuration_consistent"] = (
                            core.storage.identity_for_connection(
                                "paris", binding["credential_id"]
                            ) == binding["connection_identity_id"]
                            and core.storage.adept_profile_binding_get(
                                "identity", binding["borrower_identity_id"]
                            ) == binding["profile_id"]
                        )
                    result = {
                        "bindings": bindings,
                        "identities": [
                            {
                                "identity_id": identity.identity_id,
                                "adept_profile_id": core.storage.adept_profile_binding_get(
                                    "identity", identity.identity_id
                                ),
                            }
                            for identity in service.identities
                        ],
                        "note": "Configuration locale uniquement, sans preuve serveur Adobe",
                    }
                elif a.paris_adept_cmd == "bind":
                    # Refresh the real account inventory. A persisted
                    # connection owner alone never proves the borrower.
                    service.connect_configured()
                    result = service.bind_paris_borrower_adept(
                        credential_id=a.credential,
                        account_id=a.borrower,
                        borrower_identity_id=a.identity,
                        confirmed=True,
                    )
                elif a.paris_adept_cmd == "unbind":
                    previous = core.storage.paris_borrower_adept_binding_get(
                        credential_id=a.credential, account_id=a.borrower
                    )
                    core.storage.paris_borrower_adept_binding_delete(
                        credential_id=a.credential, account_id=a.borrower
                    )
                    result = {
                        "status": "revoked" if previous is not None else "not_found",
                        "credential_id": a.credential, "account_id": a.borrower,
                    }
                else:
                    p.error("sous-commande paris-adept inconnue")
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            except (ValueError, RuntimeError) as exc:
                print(f"paris-adept: {exc}", file=sys.stderr)
                return 2

        if a.cmd == "plugin":
            manager = core.plugins
            if a.plugin_cmd == "list":
                result = manager.diagnostic()
            elif a.plugin_cmd == "inspect":
                rows = manager.diagnostic(a.plugin_id)
                result = rows[0] if rows else {}
            elif a.plugin_cmd == "install":
                # An updater-launched CLI reports bounded phase transitions on
                # stderr. Ordinary interactive plugin installs remain unchanged.
                from .update_child import PROGRESS_ENV
                from .update_progress import update_operation, update_phase, jsonl_progress_writer
                capture = (update_operation("dev_snapshot", callback=jsonl_progress_writer(sys.stderr))
                           if os.environ.get(PROGRESS_ENV) == "1" else nullcontext())
                with capture:
                    with update_phase("plugin_inspect"):
                        archive = resolve_archive(a.archive)
                        manifest = manager.inspect_archive(archive)
                    installed = manager.install(archive, activate=not a.no_activate, replace=a.replace)
                result = {
                    "package_id": installed.manifest.package.id, "version": installed.manifest.package.version,
                    "sha256": installed.sha256, "active": manager.packages.active_version(installed.manifest.package.id),
                    "plugins": [item.id for item in manifest.plugins],
                }
            elif a.plugin_cmd == "enable":
                manager.set_enabled(a.plugin_id, True); result = manager.diagnostic(a.plugin_id)[0]
            elif a.plugin_cmd == "disable":
                manager.set_enabled(a.plugin_id, False); result = manager.diagnostic(a.plugin_id)[0]
            elif a.plugin_cmd == "remove":
                manager.remove_package(a.package_id, version=a.version); result = {"removed": a.package_id, "version": a.version}
            elif a.plugin_cmd == "rollback":
                result = {"package_id": a.package_id, "active": manager.rollback(a.package_id)}
            elif a.plugin_cmd == "diagnose":
                result = manager.diagnostic(a.plugin_id)
            elif a.plugin_cmd == "priority":
                manager.set_priority(a.plugin_id, a.value); manager.activate(); result = manager.diagnostic(a.plugin_id)[0]
            elif a.plugin_cmd == "config-get":
                result = manager.plugin_config(a.plugin_id, scope_id=a.scope_id)
            elif a.plugin_cmd == "config-set":
                values = json.loads(a.json_values)
                if not isinstance(values, dict): raise SystemExit("config-set attend un objet JSON")
                result = manager.set_plugin_config(a.plugin_id, values, scope_id=a.scope_id)
            elif a.plugin_cmd == "packages":
                result = manager.package_inventory()
            elif a.plugin_cmd == "deps-repair":
                result = manager.ensure_python_dependencies()
            else:
                raise SystemExit(f"commande plugin inconnue: {a.plugin_cmd}")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        local_adept_commands = {
            "kobo-sync-errors", "kobo-backup-errors", "kobo-resume-preview", "kobo-resume", "kobo-audit",
            "statistics", "catalog-scan", "catalog-list", "acquisition-ledger", "acquisition-reconcile",
        }
        if a.cmd not in local_adept_commands:
            if a.username:
                password = getpass.getpass("Mot de passe Bibliothèques de Paris: ")
                service.login(a.username, password)
            else:
                service.connect_configured()

        if a.account:
            account = next((x for x in service.accounts if x.credential_id == a.account), None)
            if account is None:
                raise SystemExit(f"Compte provider inconnu: {a.account}")
            service.select_account(account.unique_id, account.provider_id, account.credential_id)

        exit_code = 0
        if a.cmd == "diagnose":
            result = {
                "providers": service.provider_diagnostic_state(),
                "current_account": {
                    "provider": service.current_account.provider_id,
                    "credential_id": service.current_account.credential_id,
                    "display_name": service.current_account.display_name,
                },
                "accounts": len(service.accounts),
            }
        elif a.cmd == "loans":
            result = [safe_model(x) for x in service.refresh_loans(force=True)]
        elif a.cmd == "history":
            result = [safe_model(x) for x in service.refresh_history(force=True)]
        elif a.cmd in {"kobo-sync", "kobo-backup"}:
            progress_state = {"last_scan": 0}

            def progress(event: dict[str, Any]) -> None:
                if a.quiet:
                    return
                # Progress events may contain untrusted book titles, item IDs,
                # local file paths, plugin error codes, URLs and credentials.
                # Never echo these values to a terminal, redirected log or CI.
                kind = str(event.get("event") or "")
                number = _safe_kobo_progress_count
                if kind == "adept_probe":
                    print("[Kobo] Vérification locale Kobo-Adept…", file=sys.stderr, flush=True)
                elif kind == "adept_ready":
                    print("[Kobo] Kobo-Adept prêt", file=sys.stderr, flush=True)
                elif kind == "manifest_fetch":
                    print("[Kobo] Lecture du manifeste Kobo-Vault…", file=sys.stderr, flush=True)
                elif kind == "manifest":
                    print(f"[Kobo] Manifeste : {number(event.get('total'))} éléments", file=sys.stderr, flush=True)
                elif kind in {"recovered", "recovered_unparsed"}:
                    print(f"[Kobo] Récupération locale : {number(event.get('count'))} élément(s)", file=sys.stderr, flush=True)
                elif kind == "reconcile_required":
                    print("[Kobo] Réconciliation requise", file=sys.stderr, flush=True)
                elif kind == "reconcile_conflict":
                    print(f"[Kobo] Conflit local : {number(event.get('count'))} fichier(s)", file=sys.stderr, flush=True)
                elif kind == "scan":
                    current = number(event.get("current"))
                    total = number(event.get("total"))
                    if current == 1 or current == total or current - progress_state["last_scan"] >= 10:
                        progress_state["last_scan"] = current
                        print(
                            f"[Kobo] {current}/{total} · téléchargés={number(event.get('downloaded'))} · "
                            f"erreurs={number(event.get('errors'))}",
                            file=sys.stderr, flush=True,
                        )
                elif kind == "downloaded":
                    print("[Kobo] Téléchargement terminé", file=sys.stderr, flush=True)
                elif kind == "attention_required":
                    print("[Kobo] Action requise", file=sys.stderr, flush=True)
                elif kind == "item_error":
                    errors = number(event.get("errors"))
                    if errors <= 3 or errors % 25 == 0:
                        print(f"[Kobo] Erreur d'acquisition #{errors}", file=sys.stderr, flush=True)
                elif kind == "done":
                    print(
                        f"[Kobo] Terminé · téléchargés={number(event.get('downloaded'))} · "
                        f"à jour={number(event.get('unchanged'))} · "
                        f"action={number(event.get('attention_required'))} · "
                        f"erreurs={number(event.get('errors'))} · "
                        f"à réconcilier={number(event.get('reconcile_pending'))}",
                        file=sys.stderr, flush=True,
                    )

            result = _safe_kobo_sync_receipt(service.sync_kobo_backup(progress=progress))
            # A partial/failed/cancelled batch must not return shell success:
            # external schedulers would otherwise mark missing books as done.
            exit_code = 0 if result["status"] == "complete" else 2
        elif a.cmd in {"kobo-sync-errors", "kobo-backup-errors"}:
            from .core.kobo_backup import safe_kobo_backup_report

            # Never emit raw provider errors, book titles, product IDs,
            # local file paths or credential identifiers into a JSON report
            # users may copy into an issue or support ticket.
            result = safe_kobo_backup_report(service.kobo_backup_diagnostic())
        elif a.cmd == "kobo-audit":
            from .core.kobo_local_audit import kobo_local_reconciliation_audit

            # Offline and read-only, including no provider connector or
            # implicit scan / content download. Output never reflects IDs.
            result = kobo_local_reconciliation_audit(
                service, credential_id=a.credential,
            )
            if a.plan:
                from .core.kobo_reconciliation_plan import kobo_manual_reconciliation_plan
                # A checklist is never a write or a download authorization.
                result = {
                    "audit": result,
                    "plan": kobo_manual_reconciliation_plan(result),
                }
        elif a.cmd == "kobo-resume-preview":
            if a.refresh_manifest:
                # Only explicit --refresh-manifest may contact the Kobo
                # provider; the default preview uses persisted local state.
                service.connect_kobo_preview(a.credential)
            result = service.kobo_backup_preview(
                a.credential,
                refresh_manifest=a.refresh_manifest,
                verify_local_files=a.verify_files,
            )
        elif a.cmd == "kobo-resume":
            # The targeted provider session is connected only after explicit
            # download consent. Never connect Paris or other Kobo credentials.
            service.connect_kobo_preview(a.credential)
            summary = service.sync_kobo_backup_account(
                a.credential,
                retry_scope_blocked=a.retry_scope_blocked,
            )
            result = _safe_kobo_resume_receipt(summary)
            # Partial/attention batches must never look like completed CLI runs.
            exit_code = 0 if result["status"] == "complete" else 2
        elif a.cmd == "statistics":
            result = [service.identity_statistics(i.identity_id) for i in service.identities if not a.identity or i.identity_id==a.identity]
        elif a.cmd == "catalog-scan":
            result = service.catalog_scan(force_covers=a.refresh_covers)
        elif a.cmd == "catalog-list":
            result = service.catalog_entries(state=a.state)
        elif a.cmd == "acquisition-ledger":
            result = service.acquisition_ledger_records(status=a.status)
        elif a.cmd == "acquisition-reconcile":
            result = service.reconcile_acquisitions()
        else:
            result = [safe_model(x) for x in service.search(a.query, a.scope, force=True, provider_id=a.provider, field=a.field)]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code
    except KeyboardInterrupt:
        if getattr(a, "cmd", None) in {"kobo-sync", "kobo-backup", "kobo-resume"} and not getattr(a, "quiet", False):
            print("\n[Kobo] Synchronisation interrompue ; l’élément en cours sera repris au prochain lancement.", file=sys.stderr, flush=True)
        return 130
    finally:
        core.close()


if __name__ == "__main__":
    raise SystemExit(main())
