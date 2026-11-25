"""Explicit approval of an exact public parser-witness proposal in Search."""
from ..core.notice_parse_witness_approval import PublicWitnessProposal, public_search_witness
from ..core.search_orchestration import capture_search_read_scope
from ..core.notice_parse_witness_store import ApprovedParseWitness
from ..task_queue import TaskPriority


def request_selected_parse_witness(host, row, *, selected_id, context):
    service = host.service
    page = host.search_page
    panel = page.detail
    if getattr(row, "_core_notice_parse_input", None) is None or context is None:
        return False
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
                and getattr(host, "search_objects", {}).get(key) is row)

    def current():
        return (worker_current() and host.search_page is page and page.detail is panel
                and page.selection.get_selected_item() is cell
                and host._search_detail_context_current(selected_id, context))

    def loaded(proposal):
        if type(proposal) is not PublicWitnessProposal or not current():
            return
        from gi.repository import Gtk
        names = {"title": "titre", "record_id": "identifiant", "author": "auteur",
                 "format": "format", "publisher": "éditeur", "docbase": "base",
                 "medium": "support"}
        label = ", ".join(names[f.path[-1]] for f in proposal.fields)
        button = Gtk.Button(label=("Valider le témoin de parsing : " + label))
        button._cifereca_action_id = "approve-public-parse-witness"
        button._cifereca_action_priority = 60

        def click(_button):
            if not current() or getattr(button, "_cifereca_action_context_revoked", False):
                return
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

    host.runner.submit(
        "Lecture de la proposition de témoin…",
        lambda: public_search_witness(service, row, scope=scope) if worker_current() else None,
        loaded, lambda _error: None, priority=TaskPriority.METADATA,
        kind="detail-cache", origin="automatic",
    )
    return True
