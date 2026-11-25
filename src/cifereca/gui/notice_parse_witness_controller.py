"""Explicit approval of an exact scoped parser-witness proposal in Search."""
from ..core.notice_parse_witness_approval import (PublicWitnessProposal, public_search_witness,
                                                  public_search_witness_diagnostic,
                                                  private_search_witness, private_search_witness_diagnostic,
                                                  replay_private_search_witness)
from ..core.search_orchestration import capture_search_read_scope
from ..core.notice_parse_witness_store import ApprovedParseWitness
from ..task_queue import TaskPriority


def request_selected_parse_witness(host, row, *, selected_id, context, Gtk=None):
    # A restored private DTO is only a lookup coordinate. The worker
    # independently authorizes the exact durable source; approval still
    # requires the same-call producer receipt.
    account = getattr(row, "source_account_id", None)
    credential = getattr(row, "source_credential", None)
    private = (type(account) is str and bool(account)
               and type(credential) is str and credential not in ("", "__public__"))
    if (not private and getattr(row, "_core_notice_parse_input", None) is None) or context is None:
        return False
    witness = private_search_witness if private else public_search_witness
    inspect_witness = private_search_witness_diagnostic if private else public_search_witness_diagnostic
    service = host.service
    page = host.search_page
    panel = page.detail
    cell = page.selection.get_selected_item()
    key = getattr(cell, "key", None)
    if type(key) is not str or getattr(host, "search_objects", {}).get(key) is not row:
        return False
    try:
        scope = capture_search_read_scope(service, context[3])
    except (AttributeError, ValueError, TypeError, IndexError):
        return False
    route = getattr(host, "_route_display_token", None)
    token = object()
    panel._notice_witness_token = token

    def worker_current():
        return (not getattr(host, "_closed", False) and host.service is service
                and scope.current(service) and service.current_identity is scope.identity
                and getattr(host, "_route_display_token", None) is route
                and getattr(panel, "_notice_witness_token", None) is token
                # This existing detail guard only reads scalar generations,
                # selected ID and current identity; no GTK query on worker.
                and host._search_detail_context_current(selected_id, context)
                and getattr(host, "search_objects", {}).get(key) is row)

    def current():
        return (worker_current() and host.search_page is page and page.detail is panel
                and page.selection.get_selected_item() is cell
                and (not callable(getattr(panel, "get_mapped", None)) or panel.get_mapped()))

    def loaded(result):
        nonlocal Gtk
        if not current() or result is None:
            return
        proposal, diagnostic, replay_available = result
        if Gtk is None:
            from gi.repository import Gtk
        names = {"title": "titre", "record_id": "identifiant", "author": "auteur",
                 "format": "format", "publisher": "éditeur", "docbase": "base",
                 "medium": "support"}
        if diagnostic is not None:
            stages = {"parser_regression_confirmed": "confirmée",
                      "parser_drift_suspected": "suspectée",
                      "source_changed": "source modifiée"}
            captures = {"complete": "créée", "partial": "partielle", "failed": "échec",
                        "pending": "en attente", "capacity_refused": "capacité atteinte"}
            panel.add_group("Parsing : régression possible", [
                ("Stade", stages.get(diagnostic["status"], "indéterminé")),
                ("Archive", captures.get(diagnostic["capture_state"], "indisponible")),
            ])
            inspect = Gtk.Button(label="Inspecter le témoin de parsing")
            inspect._cifereca_action_id = ("inspect-private-parse-witness" if private else "inspect-public-parse-witness")
            def inspect_click(_button):
                if not current() or getattr(inspect, "_cifereca_action_context_revoked", False):
                    return
                inspect.set_sensitive(False)
                def inspected(value):
                    if not current():
                        return
                    if value is None:
                        host.toast("Témoin modifié ou indisponible.")
                        return
                    from datetime import datetime, timezone
                    stamp = datetime.fromtimestamp(value["last_healthy_at"], timezone.utc).isoformat()
                    panel.add_group("Preuve du témoin", [
                        ("Source", value["provider"]),
                        ("Champs atteints", ", ".join(names[n] for n in value["fields"]) or "aucun"),
                        ("Témoins atteints (cette source sélectionnée)", "1"),
                        ("Dernière observation saine", stamp),
                        ("Génération", str(value["generation"])),
                        ("Archive", captures.get(value["capture_state"], "indisponible")),
                    ])
                host.runner.submit("Inspection du témoin…",
                    lambda: inspect_witness(service, row, scope=scope)
                    if worker_current() else None, inspected, lambda _exc: inspected(None),
                    priority=TaskPriority.METADATA, kind="detail-cache", origin="user")
            inspect.connect("clicked", inspect_click)
            panel.add_actions([inspect])
        if replay_available:
            replay_button = Gtk.Button(label="Rejouer localement le témoin")
            replay_button._cifereca_action_id = "replay-private-parse-witness"
            replay_submitted = False

            def replay_click(_button):
                nonlocal replay_submitted
                if (replay_submitted or not current()
                        or getattr(replay_button, "_cifereca_action_context_revoked", False)):
                    return
                replay_submitted = True
                replay_button.set_sensitive(False)

                def work():
                    if not worker_current() or not replay_private_search_witness(
                            service, row, scope=scope):
                        return None
                    return True, inspect_witness(service, row, scope=scope)

                def completed(value):
                    if not current():
                        return
                    if value is None:
                        host.toast("Entrée locale indisponible ; aucun accès réseau.")
                        return
                    _, updated = value
                    if updated is not None and updated != diagnostic:
                        loaded((None, updated, False))
                    host.toast("Rejeu local terminé." if updated is not None
                               else "Rejeu local terminé : aucun écart actif.")

                host.runner.submit("Rejeu local du témoin…", work, completed,
                                   lambda _error: completed(None),
                                   priority=TaskPriority.METADATA, kind="detail-cache", origin="user")

            replay_button.connect("clicked", replay_click)
            panel.add_actions([replay_button])
        if type(proposal) is not PublicWitnessProposal:
            return
        label = ", ".join(names[f.path[-1]] for f in proposal.fields)
        button = Gtk.Button(label=("Valider le témoin de parsing : " + label))
        button._cifereca_action_id = ("approve-private-parse-witness" if private else "approve-public-parse-witness")
        button._cifereca_action_priority = 60

        submitted = False

        def click(_button):
            nonlocal submitted
            if (submitted or not current()
                    or getattr(button, "_cifereca_action_context_revoked", False)):
                return
            submitted = True
            button.set_sensitive(False)

            def done(result):
                if current():
                    host.toast("Témoin de parsing validé." if type(result) is ApprovedParseWitness
                               else "Témoin modifié ou expiré ; relancez la recherche.")
            def failed(_exc):
                done(None)
            host.runner.submit(
                "Validation du témoin de parsing…",
                lambda: witness(service, row, scope=scope, approved=proposal)
                if worker_current() else None,
                done, failed, priority=TaskPriority.METADATA, kind="detail-cache", origin="user",
            )

        button.connect("clicked", click)
        panel.add_actions([button])

    def read():
        if not worker_current():
            return None
        diagnostic = inspect_witness(service, row, scope=scope)
        # A degraded parse is never implicitly offered for approval.
        proposal = witness(service, row, scope=scope) if diagnostic is None else None
        replay_available = (private and replay_private_search_witness(
            service, row, scope=scope, check_only=True))
        return proposal, diagnostic, replay_available

    host.runner.submit(
        "Lecture de la proposition de témoin…",
        read,
        loaded, lambda _error: None, priority=TaskPriority.METADATA,
        kind="detail-cache", origin="automatic",
    )
    return True
