"""Explicit approval of an exact public parser-witness proposal in Search."""
from ..core.notice_parse_witness_approval import (PublicWitnessProposal, public_search_witness,
                                                  public_search_witness_diagnostic)
from ..core.search_orchestration import capture_search_read_scope
from ..core.notice_parse_witness_store import ApprovedParseWitness
from ..task_queue import TaskPriority


def request_selected_parse_witness(host, row, *, selected_id, context, Gtk=None):
    if getattr(row, "_core_notice_parse_input", None) is None or context is None:
        return False
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
        proposal, diagnostic = result
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
            inspect._cifereca_action_id = "inspect-public-parse-witness"
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
                    lambda: public_search_witness_diagnostic(service, row, scope=scope)
                    if worker_current() else None, inspected, lambda _exc: inspected(None),
                    priority=TaskPriority.METADATA, kind="detail-cache", origin="user")
            inspect.connect("clicked", inspect_click)
            panel.add_actions([inspect])
        if type(proposal) is not PublicWitnessProposal:
            return
        label = ", ".join(names[f.path[-1]] for f in proposal.fields)
        button = Gtk.Button(label=("Valider le témoin de parsing : " + label))
        button._cifereca_action_id = "approve-public-parse-witness"
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
                lambda: public_search_witness(service, row, scope=scope, approved=proposal)
                if worker_current() else None,
                done, failed, priority=TaskPriority.METADATA, kind="detail-cache", origin="user",
            )

        button.connect("clicked", click)
        panel.add_actions([button])

    def read():
        if not worker_current():
            return None
        diagnostic = public_search_witness_diagnostic(service, row, scope=scope)
        # A degraded parse is never implicitly offered for approval.
        proposal = public_search_witness(service, row, scope=scope) if diagnostic is None else None
        return proposal, diagnostic

    host.runner.submit(
        "Lecture de la proposition de témoin…",
        read,
        loaded, lambda _error: None, priority=TaskPriority.METADATA,
        kind="detail-cache", origin="automatic",
    )
    return True
