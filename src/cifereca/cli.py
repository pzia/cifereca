from __future__ import annotations

import argparse
import getpass
import json
import sys
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
    s.add_argument("--scope", choices=["all", "physical", "digital"], default="all")
    s.add_argument("--provider", default="all", help="Provider id (paris, gallica, kobo, ...)")
    s.add_argument("--field", choices=["all", "title", "author", "isbn", "subject"], default="all")
    ks_sync = sub.add_parser("kobo-sync", help="Synchronise localement les ebooks Kobo possédés")
    ks_sync.add_argument("--quiet", action="store_true", help="N’affiche pas la progression sur stderr")
    kb = sub.add_parser("kobo-backup", help="Alias historique de kobo-sync")
    kb.add_argument("--quiet", action="store_true", help="N’affiche pas la progression sur stderr")
    sub.add_parser("kobo-sync-errors", help="Diagnostic local de la synchronisation Kobo")
    sub.add_parser("kobo-backup-errors", help="Alias historique de kobo-sync-errors")
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

    if a.cmd == "identity-directory" and a.abort:
        try:
            result = CiferecaCore.abort_directory_migration()
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr); return 1

    core = CiferecaCore.open(diagnostics_enabled=True, diagnostics_level="detailed")
    service = core.library
    try:
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
        if a.cmd == "plugin":
            manager = core.plugins
            if a.plugin_cmd == "list":
                result = manager.diagnostic()
            elif a.plugin_cmd == "inspect":
                rows = manager.diagnostic(a.plugin_id)
                result = rows[0] if rows else {}
            elif a.plugin_cmd == "install":
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
            "kobo-sync-errors", "kobo-backup-errors",
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
                kind = str(event.get("event") or "")
                if kind == "adept_probe":
                    print("[Kobo] Synchronisation locale · vérification de Kobo-Adept…", file=sys.stderr, flush=True)
                elif kind == "adept_ready":
                    print(
                        f"[Kobo] Adept {event.get('software_version') or '?'} · contrat v{event.get('contract_version') or '?'}",
                        file=sys.stderr, flush=True,
                    )
                elif kind == "manifest_fetch":
                    print("[Kobo] Lecture du manifest Kobo-Vault…", file=sys.stderr, flush=True)
                elif kind == "manifest":
                    print(f"[Kobo] {event.get('total', 0)} élément(s) dans le manifest", file=sys.stderr, flush=True)
                elif kind == "recovered":
                    print(
                        f"[Kobo] Reprise de {event.get('count', 0)} acquisition(s) interrompue(s)",
                        file=sys.stderr, flush=True,
                    )
                elif kind == "recovered_unparsed":
                    print(
                        f"[Kobo] {event.get('count', 0)} ancien(s) résultat(s) JSON non réconcilié(s) détecté(s)",
                        file=sys.stderr, flush=True,
                    )
                elif kind == "reconcile_required":
                    print(
                        f"[Kobo] ? réconciliation requise · {event.get('title') or event.get('item_id') or ''}",
                        file=sys.stderr, flush=True,
                    )
                elif kind == "reconcile_conflict":
                    print(
                        f"[Kobo] ! conflit local ({event.get('count', 0)} fichiers) · "
                        f"{event.get('title') or event.get('item_id') or ''}",
                        file=sys.stderr, flush=True,
                    )
                elif kind == "scan":
                    current = int(event.get("current") or 0)
                    total = int(event.get("total") or 0)
                    if current == 1 or current == total or current - progress_state["last_scan"] >= 10:
                        progress_state["last_scan"] = current
                        print(
                            f"[Kobo] {current}/{total} · téléchargés={event.get('downloaded', 0)} · "
                            f"erreurs={event.get('errors', 0)} · {event.get('title') or ''}",
                            file=sys.stderr, flush=True,
                        )
                elif kind == "downloaded":
                    print(
                        f"[Kobo] ✓ {event.get('title') or event.get('item_id')} → {event.get('path') or ''}",
                        file=sys.stderr, flush=True,
                    )
                elif kind == "attention_required":
                    print(
                        f"[Kobo] ? action requise ({event.get('code') or '?'}) · {event.get('title') or event.get('item_id') or ''}",
                        file=sys.stderr, flush=True,
                    )
                elif kind == "item_error":
                    errors = int(event.get("errors") or 0)
                    if errors <= 3 or errors % 25 == 0:
                        print(
                            f"[Kobo] ! erreur #{errors} ({event.get('code') or '?'}) · {event.get('title') or ''}",
                            file=sys.stderr, flush=True,
                        )
                elif kind == "done":
                    print(
                        f"[Kobo] Terminé · téléchargés={event.get('downloaded', 0)} · "
                        f"à jour={event.get('unchanged', 0)} · action={event.get('attention_required', 0)} · "
                        f"erreurs={event.get('errors', 0)} · à réconcilier={event.get('reconcile_pending', 0)}",
                        file=sys.stderr, flush=True,
                    )

            result = service.sync_kobo_backup(progress=progress)
        elif a.cmd in {"kobo-sync-errors", "kobo-backup-errors"}:
            result = service.kobo_backup_diagnostic()
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
        return 0
    except KeyboardInterrupt:
        if getattr(a, "cmd", None) in {"kobo-sync", "kobo-backup"} and not getattr(a, "quiet", False):
            print("\n[Kobo] Synchronisation interrompue ; l’élément en cours sera repris au prochain lancement.", file=sys.stderr, flush=True)
        return 130
    finally:
        core.close()


if __name__ == "__main__":
    raise SystemExit(main())
