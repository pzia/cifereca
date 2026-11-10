from __future__ import annotations

import argparse
import getpass
import json
import logging
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
    from .core.legacy_diagnostic_projection import _safe_kobo_backup_diagnostic_detail

    # This serializer is also used by Diagnostics.add; it rejects all free
    # text, account identifiers, titles, signed URLs, local paths and tokens.
    return {"redacted": True, **_safe_kobo_backup_diagnostic_detail(summary)}


def _kobo_legacy_report_view(
    safe_report: dict[str, Any], *, observed_at: int | None = None,
) -> dict[str, Any]:
    """CLI-only projection: legacy checkpoint ages are not current Core errors.

    Input MUST already be redacted by safe_kobo_backup_report. Keep the
    historical counts unchanged and never join legacy IDs to a Core cycle.
    """
    import time

    if type(safe_report) is not dict or safe_report.get("redacted") is not True:
        raise ValueError("unsafe Kobo diagnostic input")
    as_of = int(time.time()) if observed_at is None else observed_at
    valid_clock = type(as_of) is int and 946_684_800 <= as_of <= 4_102_444_800
    freshness: list[dict[str, Any]] = []
    sources = safe_report.get("sources")
    if type(sources) is list:
        for source in sources[:25]:
            if type(source) is not dict:
                continue
            index, last = source.get("source_index"), source.get("updated_at")
            if type(index) is not int or not 1 <= index <= 25:
                continue
            age_known = (
                valid_clock and type(last) is int
                and 946_684_800 <= last <= as_of
                and as_of - last <= 1_000_000_000
            )
            row: dict[str, Any] = {
                "source_index": index, "age_known": bool(age_known),
            }
            if age_known:
                row["age_seconds"] = as_of - last
            freshness.append(row)
    return {
        **safe_report,
        "status_authority": "historical_legacy_backup",
        "current_canonical_cycle": {
            "status": "not_evaluated", "same_source_proof": "unavailable",
        },
        "legacy_checkpoint_freshness": freshness,
    }


def _export_parent_is_safe(destination: Path) -> bool:
    """Check known output-path blockers before Core or provider I/O.

    This is only an early usability/safety preflight; the atomic writers
    independently enforce O_EXCL, mode 0600 and symlink protections again
    at publication time to handle filesystem races.
    """
    try:
        target = Path(os.path.abspath(destination.expanduser()))
        return target.parent.is_dir() and not any(
            component.is_symlink() for component in (target, *target.parents)
        )
    except (OSError, ValueError, RuntimeError):
        return False


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
    acquisition_audit = sub.add_parser(
        "acquisition-audit", help="Audit canonique hors réseau d'une source exacte"
    )
    acquisition_audit.add_argument("--provider", default="")
    acquisition_audit.add_argument("--identity", default="")
    acquisition_audit.add_argument("--credential", default="")
    acquisition_audit.add_argument("--account-id", default=None)
    acquisition_audit.add_argument("--verify-files", action="store_true")
    acquisition_audit.add_argument("--json", action="store_true")
    acquisition_audit.add_argument("--output", type=Path, metavar="JSON")
    legacy_inventory = sub.add_parser(
        "legacy-backup-inventory",
        help="Inventaire SQLite Kobo 0.7 borné et hors réseau, sans migration",
    )
    legacy_inventory.add_argument("--identity", default="")
    legacy_inventory.add_argument("--json", action="store_true")
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
        "--verify-files", action="store_true",
        help="Vérifier les fichiers et SHA-256 dans les audits du rapport ZIP"
    )
    generic_sub.choices["run"].add_argument(
        "--report", type=Path, metavar="ZIP",
        help="Rapport local expurgé de synchronisation (compteurs SQLite et phases CLI)"
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
    reconcile = sub.add_parser(
        "kobo-reconcile-local", help="Commande legacy retirée : relink canonique attesté uniquement"
    )
    reconcile.add_argument("--credential", required=True)
    reconcile.add_argument("--identity", help="Refuser une connexion liée à une autre identité")
    reconcile.add_argument("--account-id", required=True)
    reconcile.add_argument("--item", required=True)
    reconcile.add_argument("--sha256", required=True, help="SHA déjà attesté dans le ledger, jamais inféré du titre")
    reconcile.add_argument("--path", help="Fichier EPUB/PDF dans le dossier de cette identité")
    reconcile.add_argument("--undo", help="Identifiant opaque du rapprochement à annuler (aperçu par défaut)")
    reconcile.add_argument("--confirm", help="Token de l’aperçu exact ; son absence interdit toute écriture")
    # P0 #4859: existing canonical B path repair, no 0.7 inference.
    relink_b = sub.add_parser(
        "kobo-relink-canonical",
        help="Relocaliser un Placement Kobo déjà attesté A→B, sans droit nouveau",
    )
    relink_b.add_argument("--credential", required=True)
    relink_b.add_argument("--identity", required=True)
    relink_b.add_argument("--account-id", required=True)
    relink_b.add_argument("--item", required=True)
    relink_b.add_argument("--right", required=True)
    relink_b.add_argument("--branch", required=True)
    relink_b.add_argument(
        "--kind", choices=("purchase", "loan"), default="purchase",
        help="Type exact de SourceReference déjà publié (défaut : purchase)",
    )
    relink_b.add_argument("--placement", required=True)
    relink_b.add_argument("--path", required=True)
    relink_b.add_argument("--confirm", help="Token exact d'aperçu, sinon lecture seule")
    relink_b.add_argument(
        "--undo", help="Opération de relocalisation canonique à inverser ; "
                       "remettre physiquement le fichier au chemin d'origine avant l'aperçu",
    )
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
    # P0 #4858 D3: one edition only, using Core's same selected-offer A->B.
    # The legacy kobo-resume command stays isolated from this route.
    selected_kobo = sub.add_parser(
        "kobo-sync-edition",
        help="Livraison canonique d'une édition Kobo exacte (consentement requis)",
    )
    selected_kobo.add_argument("--credential", required=True)
    selected_kobo.add_argument("--item", required=True)
    selected_kobo.add_argument(
        "--trace", action="store_true",
        help="Diagnostic privé local : conserve les URL de livraison signées dans le journal",
    )
    selected_kobo.add_argument(
        "--confirm", action="store_true",
        help="Je confirme la livraison de cette édition par le Core",
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
    adept_validate = adept_sub.add_parser(
        "validate-resolution-snapshot",
        help="Revérifie une empreinte de preuves locales Paris/ADEPT sans mutation",
    )
    adept_validate.add_argument("--loan-id", required=True)
    adept_validate.add_argument("--item-id", required=True)
    adept_validate.add_argument("--fingerprint", required=True)
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
            "audit-origin", "audit-canonical", "audit-transaction",
            "preview-resolution", "validate-resolution-snapshot",
        }:
            # Reject unusable transaction keys before Core can be opened.
            if (not a.loan_id.strip() or not a.item_id.strip()
                    or "\x00" in a.loan_id or "\x00" in a.item_id):
                p.error("audit de transaction exige --loan-id et --item-id non vides et sans NUL")
        if a.paris_adept_cmd == "validate-resolution-snapshot":
            if (a.loan_id != a.loan_id.strip() or a.item_id != a.item_id.strip()
                    or "\r" in a.loan_id or "\n" in a.loan_id
                    or "\r" in a.item_id or "\n" in a.item_id
                    or len(a.fingerprint) != 64
                    or any(ch not in "0123456789abcdef" for ch in a.fingerprint)):
                p.error("validate-resolution-snapshot exige une empreinte SHA-256 exacte")
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

    # P0 #3196: inventory the *existing* v14 SQLite in ro mode without
    # constructing CiferecaCore/Storage or connecting a supplier. Do not
    # expose the old product IDs, paths or credentials in terminal output.
    if a.cmd == "legacy-backup-inventory":
        from .core.legacy_backup_inventory_cli import inventory_existing_legacy_kobo
        if a.username or a.account or not a.identity:
            print(json.dumps({"status": "failed", "code": "identity_scope_required"}))
            return 2
        inventory = inventory_existing_legacy_kobo(identity_id=a.identity)
        if inventory["status"] != "complete":
            print(json.dumps({"status": "failed", "code": inventory["status"]}))
            return 2
        print(json.dumps({"status": "success", "inventory": inventory},
                         ensure_ascii=False, indent=2))
        return 0

    # A local repair/preview must not activate plugins, chmod Config, purge
    # caches or recover a directory migration as a side effect of startup.
    if a.cmd == "kobo-reconcile-local":
        # P0 #4859 W4/D5: the retired v0.7 positive repair treated legacy
        # backup_items + generic ledger as authority for an applied delivery.
        # Deny even preview and undo: a historical token cannot authorize
        # a new positive mutation after this cutover, and the CLI must never
        # open SQLite or inspect a private file on this deprecated route.
        # Only independently published canonical B may be relinked.
        print(json.dumps({
            "ok": False, "offline": True, "changed": 0,
            "reason": "legacy_reconciliation_retired",
        }, ensure_ascii=False, indent=2))
        return 2

    if a.cmd == "kobo-relink-canonical":
        from .core.kobo_local_reconciliation import (
            ReconciliationRejected, reconcile_existing_profile,
        )
        from .core.canonical_relink import CanonicalRelinkRejected
        try:
            if a.account and a.account != a.credential:
                raise ReconciliationRejected("source_scope_mismatch")
            result = reconcile_existing_profile(
                canonical_relink=True, credential_id=a.credential,
                identity_id=a.identity, account_id=a.account_id,
                edition_id=a.item, right_id=a.right,
                branch_id=a.branch, reference_kind=a.kind,
                placement_id=a.placement, path=a.path,
                confirmation=a.confirm,
                **({"undo_operation": a.undo} if a.undo is not None else {}),
            )
        except Exception as exc:
            result = {
                "status": "failed", "ok": False, "changed": 0,
                "reason": (
                    exc.code if isinstance(exc, (
                        ReconciliationRejected, CanonicalRelinkRejected,
                    )) else "canonical_relink_unavailable"
                ),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2

    if a.cmd == "kobo-sync-edition":
        # No Core startup or provider I/O for unconfirmed CLI invocations.
        if not a.confirm or a.account or a.username:
            print(json.dumps({
                "status": "failed",
                "code": ("explicit_edition_confirmation_required" if not a.confirm
                         else "legacy_account_selector_unsupported"),
            }))
            return 2

    if a.cmd == "acquisition-audit":
        if a.username or a.account or not a.identity or not a.provider:
            print(json.dumps({"status": "failed", "code": "source_scope_required"}))
            return 2
        if a.output is not None and os.path.lexists(a.output):
            print(json.dumps({"status": "failed", "code": "audit_destination_exists"}))
            return 2
        if a.output is not None and not _export_parent_is_safe(a.output):
            print(json.dumps({"status": "failed", "code": "audit_destination_unavailable"}))
            return 2

    if a.cmd == "sync" and a.sync_cmd == "run":
        from .core.sync_cli_contract import SyncSelectionError
        # Reject unsafe/incomplete requests before opening Core, loading
        # plugins or creating a provider session.
        preflight_code = None
        if a.username or a.account:
            preflight_code = "legacy_account_selector_unsupported"
        elif not a.confirm:
            preflight_code = "explicit_run_confirmation_required"
        elif not a.identity or not a.provider:
            preflight_code = "source_scope_required"
        elif a.trace_export is not None and not a.confirm_trace_export:
            preflight_code = "trace_export_confirmation_required"
        elif a.confirm_trace_export and a.trace_export is None:
            preflight_code = "trace_export_destination_required"
        elif (a.report is not None and a.trace_export is not None
              and os.path.abspath(a.report) == os.path.abspath(a.trace_export)):
            # A sensitive raw HTTP archive must NEVER masquerade as the
            # redacted acquisition report at the same selected destination.
            preflight_code = "report_trace_destination_conflict"
        elif a.verify_files and a.report is None:
            preflight_code = "report_required_for_file_verification"
        elif a.report is not None and os.path.lexists(a.report):
            preflight_code = "report_destination_exists"
        elif a.trace_export is not None and os.path.lexists(a.trace_export):
            # Refuse before any remote provider connection or acquisition.
            # Core export also checks O_EXCL against symlink/concurrent races.
            preflight_code = "trace_export_destination_exists"
        elif a.report is not None and not _export_parent_is_safe(a.report):
            preflight_code = "report_destination_unavailable"
        elif a.trace_export is not None and not _export_parent_is_safe(a.trace_export):
            preflight_code = "trace_export_destination_unavailable"
        if preflight_code is not None:
            print(json.dumps({"status": "failed", "code": preflight_code},
                             ensure_ascii=False))
            return 2

    private_kobo_trace = a.cmd == "kobo-sync-edition" and a.trace
    core = CiferecaCore.open(
        diagnostics_enabled=True,
        diagnostics_level="trace" if private_kobo_trace else "detailed",
    )
    service = core.library
    kobo_trace_previous_level = None
    kobo_trace_handlers_before = None
    try:
        if private_kobo_trace:
            # The standalone CLI does not set up the GUI's file logger.
            # Establish the same 0600 private journal only for explicit
            # --trace, then remove newly installed handlers on exit.
            from .logging_utils import (
                configure_logging, diagnostics_level, set_diagnostics_level,
            )
            kobo_trace_previous_level = diagnostics_level()
            kobo_trace_handlers_before = tuple(logging.getLogger("cifereca").handlers)
            configure_logging()
            set_diagnostics_level("trace")
        if a.cmd == "kobo-sync-edition":
            from .core.canonical_kobo_selected import SelectedKoboRejected
            try:
                # Reuse the existing exact-credential connect gate, never
                # connect every Kobo account or infer source from an item.
                # No provider activity happens without CLI --confirm.
                # Unlike a read-only preview, the canonical exact-edition
                # executor needs one live Account bound to its persisted
                # identity/source coordinates. This connector verifies that
                # binding without enrolling or connecting unrelated accounts.
                service.connect_kobo_selected_edition(a.credential)
                result = service.synchronize_kobo_selected_edition(
                    a.credential, a.item, explicit_user_action=True,
                )
            except SelectedKoboRejected as exc:
                result = {"status": "failed", "ok": False, "changed": 0,
                          "reason": exc.code}
            except Exception:
                # Provider errors can contain URLs, paths and private account
                # names. Never emit their original messages.
                result = {"status": "failed", "ok": False, "changed": 0,
                          "reason": "selected_delivery_unavailable"}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2
        if a.cmd == "acquisition-audit":
            from types import SimpleNamespace
            from .core.sync_cli_contract import SyncSelectionError, require_unique_sync_account
            from .core.acquisition_audit import (
                inspect_exact_source, safe_acquisition_audit, write_acquisition_audit,
            )
            try:
                # Persisted local bindings only: no plugin discovery, login,
                # remote inventory, rights synchronization or file mutation.
                eligible = [
                    SimpleNamespace(
                        identity_id=row["identity_id"],
                        provider_id=row["provider_id"],
                        credential_id=row["credential_id"],
                        unique_id=row["account_id"],
                    )
                    for row in core.storage.provider_accounts()
                ]
                account = require_unique_sync_account(
                    eligible, provider=a.provider, identity=a.identity,
                    credential=a.credential, account_id=a.account_id,
                )
                root = service.library_dir() if a.verify_files else None
                audit = safe_acquisition_audit(inspect_exact_source(
                    core.storage, account, library_root=root,
                    verify_files=a.verify_files,
                ))
                result = {"status": "success", "audit": audit}
                if a.output is not None:
                    write_acquisition_audit(a.output, audit)
                    result = {"status": "success", "output": "created"}
                code = 0
            except SyncSelectionError as exc:
                result = {"status": "failed", "code": str(exc)}
                code = 2
            except FileExistsError:
                result = {"status": "failed", "code": "audit_destination_exists"}
                code = 2
            except Exception:
                result = {"status": "failed", "code": "audit_unavailable"}
                code = 2
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return code
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
            report_account = None
            report_before = None
            report_ref = None
            report_secret = None
            report_library_root = None
            report_started_at = None
            report_events = []
            report_core_events = []
            report_active_phase = None
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
                    if a.report is not None:
                        from datetime import datetime, timezone
                        import secrets
                        from .core.sync_report import audit_source_counts
                        # Source selection is complete; record the run BEFORE
                        # provider connection so connection failures are audited.
                        report_account = resolved
                        report_events.append({"phase": "selected", "status": "success"})
                        report_ref = secrets.token_hex(16)
                        report_secret = secrets.token_bytes(32)
                        if a.verify_files:
                            try:
                                report_library_root = service.library_dir()
                            except Exception:
                                report_library_root = None
                        report_started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        report_active_phase = "audit_before"
                        try:
                            report_before = audit_source_counts(
                                core.storage, resolved, pseudonym_key=report_secret,
                                library_root=report_library_root, verify_files=a.verify_files,
                            )
                        except Exception:
                            report_before = {"status": "unavailable", "reason": "audit_unavailable"}
                        report_events.append({
                            "phase": "audit_before",
                            "status": "unavailable" if report_before.get("status") == "unavailable"
                            else "success",
                        })
                        report_active_phase = "connect"
                        report_events.append({"phase": "connect", "status": "started"})
                    service.connect_selected_source(
                        identity=resolved.identity_id,
                        provider=resolved.provider_id,
                        credential=resolved.credential_id,
                    )
                    if report_account is not None:
                        report_events.append({"phase": "connect", "status": "success"})
                        report_active_phase = None
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
                    if report_account is not None:
                        report_active_phase = "sync"
                        report_events.append({"phase": "sync", "status": "started"})
                    if report_account is not None:
                        from .core.sync_report import collect_source_sync_core_events
                        # The in-memory observer is scoped to this exact
                        # synchronous invocation and exact selected provider.
                        with collect_source_sync_core_events(account.provider_id) as report_core_events:
                            raw = service.synchronize_account_source(account)
                    else:
                        raw = service.synchronize_account_source(account)
                    result = {"provider": account.provider_id,
                              "identity": account.identity_id,
                              **safe_sync_result(raw)}
                    if report_account is not None:
                        report_events.append({
                            "phase": "sync", "status": result["status"],
                            "code": result.get("code"),
                        })
                        report_active_phase = None
                    code = 0 if result["status"] in ("success", "complete") else 2
            except KeyboardInterrupt:
                # An explicit run interrupted before source admission retains
                # the original shell interruption and does not publish a ZIP.
                if report_account is None:
                    raise
                result = {"status": "cancelled", "code": "cancelled"}
                code = 130
                if report_active_phase is not None:
                    report_events.append({
                        "phase": report_active_phase, "status": "cancelled",
                        "code": "cancelled",
                    })
                    report_active_phase = None
            except SyncSelectionError as exc:
                result = {"status": "failed", "code": str(exc)}
                code = 2
                if report_account is not None and report_active_phase is not None:
                    report_events.append({
                        "phase": report_active_phase, "status": "failed",
                        "code": str(exc),
                    })
            except Exception:
                result = {"status": "failed", "code": "sync_unavailable"}
                code = 2
                if report_account is not None and report_active_phase is not None:
                    report_events.append({
                        "phase": report_active_phase, "status": "failed",
                        "code": "sync_unavailable",
                    })
            finally:
                if trace_started:
                    try:
                        from .http_trace import HTTP_TRACE
                        # O_EXCL, atomic ZIP and 0600 are enforced by Core.
                        # Capture is opt-in and never uploaded or echoed.
                        HTTP_TRACE.export(a.trace_export)
                        trace_outcome = "created"
                    except KeyboardInterrupt:
                        trace_outcome = "cancelled"
                        if report_account is None:
                            raise
                        result = {"status": "cancelled", "code": "cancelled"}
                        code = 130
                    except Exception:
                        trace_outcome = "export_failed"
                    finally:
                        set_diagnostics_level(trace_previous_level)
                    if report_account is not None:
                        report_events.append({
                            "phase": "trace_export",
                            "status": (
                                "cancelled" if trace_outcome == "cancelled"
                                else "success" if trace_outcome == "created" else "failed"
                            ),
                        })
            # The report's exit_code must equal the command's eventual exit,
            # including an independently requested raw HTTP trace export failure.
            # Trace bytes remain outside the redacted report regardless.
            if trace_requested:
                result["http_trace"] = trace_outcome
                if trace_outcome == "export_failed" and code != 130:
                    code = 2
            if a.sync_cmd == "run" and a.report is not None and report_account is not None:
                from .core.sync_report import audit_source_counts, write_sync_report
                try:
                    try:
                        report_after = audit_source_counts(
                            core.storage, report_account, pseudonym_key=report_secret,
                            library_root=report_library_root, verify_files=a.verify_files,
                        )
                    except KeyboardInterrupt:
                        # Keep the FIRST interrupted phase if cancellation was
                        # already established during an earlier CLI operation.
                        # A second SIGINT while collecting the optional final
                        # snapshot must not erase its original diagnosis.
                        report_after = {"status": "unavailable", "reason": "audit_unavailable"}
                        if code != 130:
                            result = {"status": "cancelled", "code": "cancelled"}
                            code = 130
                            report_events.append({
                                "phase": "audit_after", "status": "cancelled",
                                "code": "cancelled",
                            })
                    except Exception:
                        report_after = {"status": "unavailable", "reason": "audit_unavailable"}
                    report_events.append({
                        "phase": "audit_after",
                        "status": "unavailable" if report_after.get("status") == "unavailable"
                        else "success",
                    })
                    write_sync_report(
                        a.report, account=report_account, run_ref=report_ref,
                        before=report_before, after=report_after,
                        result=result, started_at=report_started_at, exit_code=code,
                        cli_events=report_events, core_events=report_core_events,
                    )
                    result["report"] = "created"
                except Exception:
                    result["report"] = "export_failed"
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
                    from .core.adept_resolution_checkpoint import (
                        preview_paris_adept_with_local_checkpoint,
                    )
                    # Preview only: user-selected refs are not authorization
                    # tokens. No ADEPT return or re-fulfillment is invoked.
                    result = preview_paris_adept_with_local_checkpoint(
                        core.storage, loan_id=a.loan_id.strip(),
                        item_id=a.item_id.strip(),
                        source_reference_id=a.source_reference_id,
                        acquisition_ref=a.acquisition_ref,
                        artifact_inspector=inspect_artifact,
                    )
                elif a.paris_adept_cmd == "validate-resolution-snapshot":
                    from .core.adept_resolution_snapshot import (
                        validate_paris_adept_local_snapshot,
                    )
                    # Local equality only, NEVER Adobe inventory attestation.
                    result = validate_paris_adept_local_snapshot(
                        core.storage, loan_id=a.loan_id,
                        item_id=a.item_id,
                        expected_fingerprint=a.fingerprint,
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
            "kobo-sync-errors", "kobo-backup-errors", "kobo-resume-preview", "kobo-resume",
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
            from .core.legacy_diagnostic_projection import safe_kobo_backup_report

            # Never emit raw provider errors, book titles, product IDs,
            # local file paths or credential identifiers into a JSON report
            # users may copy into an issue or support ticket.
            result = _kobo_legacy_report_view(
                safe_kobo_backup_report(service.kobo_backup_diagnostic()),
            )
        elif a.cmd == "kobo-resume-preview":
            try:
                if a.refresh_manifest:
                    # Only the explicit refresh may read the remote Kobo A
                    # inventory. A complete ABI also needs its one exact
                    # already-bound Core Account: preview-only session
                    # registration cannot satisfy the canonical A reader.
                    # This memory-only bind does NOT grant GET/download:
                    # the preview's pipeline is read-only and every actual
                    # acquisition has independent CAS/confirmation gates.
                    # Incomplete ABIs retain their bounded legacy preview.
                    service.connect_kobo_canonical(a.credential)
                result = service.kobo_backup_preview(
                    a.credential,
                    refresh_manifest=a.refresh_manifest,
                    verify_local_files=a.verify_files,
                    offline_canonical_only=not a.refresh_manifest,
                )
            except Exception:
                # Diagnostic CLI boundary: provider responses, private paths,
                # signed URLs and tokens must not escape as a traceback.
                # No fallback to another identity or credential is allowed.
                result = {
                    "status": "unavailable",
                    "code": "kobo_preview_unavailable",
                    "preview_only": True,
                    "acquisition_authorized": False,
                }
                exit_code = 2
        elif a.cmd == "kobo-resume":
            # Only after --confirm-downloads: activate the exact existing
            # borrower in Core memory for modern canonical A→B. Partial ABI
            # remains on the old bounded compatibility route; no other
            # provider/account is connected or registered in SQLite.
            service.connect_kobo_canonical(a.credential)
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
        if kobo_trace_previous_level is not None:
            from .logging_utils import set_diagnostics_level
            logger = logging.getLogger("cifereca")
            for handler in tuple(logger.handlers):
                if handler not in kobo_trace_handlers_before:
                    logger.removeHandler(handler)
                    handler.close()
            set_diagnostics_level(kobo_trace_previous_level)
        core.close()


if __name__ == "__main__":
    raise SystemExit(main())
