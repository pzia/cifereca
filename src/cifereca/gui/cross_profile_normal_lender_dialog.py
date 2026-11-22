"""#4988: explicit consent for the normal lender path, without copy rights."""
from __future__ import annotations

from threading import Lock

from ..core.borrow_for_identity_discovery import (
    possible_lender_matches, possible_lender_session_matches,
)


def present_normal_lender_choice(panel, *, item, rows, current, service,
                                 identity, identity_id, account_epoch,
                                 source_epoch, Adw, TaskPriority, control=None):
    """Choose among multiple candidates, then require explicit confirmation."""
    window = panel.window
    execute = getattr(service, "execute_normal_lender_borrow_for_current_identity", None)

    def lender_current(row):
        return (possible_lender_matches(
            row, item, requesting_identity_id=identity_id,
        ) and (row._session is None or possible_lender_session_matches(
            row, row._session,
        )))
    if (not callable(execute) or not current()
            or type(rows) is not tuple or not 1 <= len(rows) <= 64
            or any(not lender_current(row) for row in rows)
            or getattr(window, "_cross_profile_borrow_pending", None) is not None):
        return False
    token = object()
    window._cross_profile_borrow_pending = token
    stage = "choose"
    route = getattr(window, "_route_display_token", None)
    visible = getattr(panel, "_rendered_search_detail", None)
    ui_identity = getattr(window, "_identity_context_generation", None)
    ui_account = getattr(window, "_account_context_generation", None)
    from collections import Counter
    counts = Counter(row.label for row in rows)
    labels = {
        id(row): (f"{row.label} · choix {index + 1}/{len(rows)}"
                  if counts[row.label] > 1 else row.label)
        for index, row in enumerate(rows)
    }

    def route_current():
        # This generation is plain owner state; no GTK read on the worker.
        return (not getattr(window, "_closed", False)
                and window.service is service
                and getattr(panel, "_rendered_search_detail", None) is visible
                and getattr(window, "_route_display_token", None) is route
                and getattr(window, "_identity_context_generation", None) == ui_identity
                and getattr(window, "_account_context_generation", None) == ui_account)

    def dialog_current():
        return route_current() and current()

    def owns(expected):
        return stage == expected and getattr(window, "_cross_profile_borrow_pending", None) is token

    def release():
        nonlocal stage
        stage = "done"
        if getattr(window, "_cross_profile_borrow_pending", None) is token:
            window._cross_profile_borrow_pending = None

    def confirmed(candidate, response):
        nonlocal stage
        if not owns("confirm"):
            return
        if (type(response) is not str or response != "borrow" or not dialog_current()
                or not lender_current(candidate)):
            release()
            return
        stage = "submitted"
        dispatch_lock = Lock()
        dispatched = False

        def work():
            nonlocal dispatched
            # A rejected submission or completed dialog cannot dispatch a
            # retained worker. Consume this local dispatch at most once;
            # Core still owns the durable claim and all execution authority.
            with dispatch_lock:
                if dispatched or not owns("submitted"):
                    return {"status": "blocked"}
                dispatched = True
            # No widget reads on the worker. Core revalidates exact candidate,
            # requester object, epochs, lender/session and at-most-once claim.
            if (not route_current() or not lender_current(candidate)):
                return {"status": "blocked"}
            return execute(
                captured_candidate=candidate, exact_notice=item,
                expected_requesting_identity_id=identity_id,
                expected_requesting_identity=identity,
                expected_context_epoch=account_epoch,
                expected_source_epoch=source_epoch, confirmed=True,
            )

        def done(result):
            if not owns("submitted"):
                return
            release()
            if not dialog_current():
                return
            if (type(result) is dict and result.get("status") == "confirmed"
                    and result.get("provider_verified") is True
                    and result.get("delivery") == "unavailable"
                    and result.get("transfer") == "blocked"
                    and result.get("retry_borrow") is False):
                window._activity_result(
                    "Prêt confirmé sur le profil prêteur. Livraison non vérifiée ; aucune copie lancée."
                )
            elif type(result) is dict and result.get("status") == "blocked":
                window.show_error("Ce choix n'est plus admissible.", "Emprunt entre profils")
            else:
                window.show_error(
                    "Résultat non vérifié : consultez les prêts du profil prêteur. Aucun nouvel essai automatique.",
                    "Emprunt entre profils",
                )

        def failed(_error):
            done(None)

        try:
            window.runner.submit("Emprunt sur le profil prêteur…", work, done, failed,
                                 control=control, priority=TaskPriority.USER_ACTION, kind="borrow")
        except Exception:
            failed(None)

    def present_confirmation(candidate):
        nonlocal stage
        if not owns("choose"):
            return False
        if not dialog_current() or not lender_current(candidate):
            release()
            return False
        stage = "confirm"
        try:
            menu = Adw.MessageDialog.new(
                window, "Confirmer l'emprunt sur le profil prêteur ?",
                f"{labels[id(candidate)]} — offre et quota non vérifiés. "
                "Le prêt sera enregistré sur ce compte si le fournisseur l'accepte. "
                "Aucune copie n'est autorisée par cette confirmation.",
            )
            menu.add_response("cancel", "Annuler")
            menu.add_response("borrow", "Emprunter sur ce profil")
            menu.connect("response", lambda _dialog, value: confirmed(candidate, value))
            menu.present()
            return True
        except Exception:
            release()
            return False

    if len(rows) == 1:
        return present_confirmation(rows[0])

    choices = {f"choice-{i}": row for i, row in enumerate(rows)}

    def selected(_dialog, response):
        present_confirmation(choices.get(response) if type(response) is str else None)

    try:
        menu = Adw.MessageDialog.new(
            window, "Choisir le profil prêteur",
            "Choix local uniquement : offre et quota non vérifiés.",
        )
        menu.add_response("cancel", "Annuler")
        for response, row in choices.items():
            menu.add_response(response, labels[id(row)])
        menu.connect("response", selected)
        if not dialog_current():
            release()
            return False
        menu.present()
        return True
    except Exception:
        release()
        return False


def append_normal_lender_action(panel, *, item, rows, visible, service,
                                identity, identity_id, account_epoch, source_epoch, route):
    """Attach an optional consent entry to the existing shared detail action bar."""
    window = panel.window
    if (getattr(window, "_route_display_token", None) is not route
            or type(rows) is not tuple or not 1 <= len(rows) <= 64
            or not callable(getattr(panel, "_actions", None))
            or not callable(getattr(service, "execute_normal_lender_borrow_for_current_identity", None))
            or any(not possible_lender_matches(
                row, item, requesting_identity_id=identity_id,
            ) or (row._session is not None and not possible_lender_session_matches(
                row, row._session,
            )) for row in rows)):
        return False
    try:
        ui_identity = window._identity_context_generation
        ui_account = window._account_context_generation
        from gi.repository import Adw
        from .action_widgets import action_button
        from .task_runner import TaskPriority
    except (AttributeError, ImportError):
        return False

    def current():
        try:
            return (not getattr(window, "_closed", False)
                    and not getattr(button, "_cifereca_action_context_revoked", False)
                    and getattr(window, "_route_display_token", None) is route
                    and panel.get_mapped()
                    and panel._rendered_search_detail is visible
                    and window.service is service
                    and service.current_identity is identity
                    and identity.identity_id == identity_id
                    and service._account_context_epoch == account_epoch
                    and service._source_context_epoch == source_epoch
                    and window._identity_context_generation == ui_identity
                    and window._account_context_generation == ui_account)
        except Exception:
            return False

    label = f"Emprunter avec {rows[0].label}…" if len(rows) == 1 else "Emprunter sur et avec…"
    button = action_button(label, "system-users-symbolic")
    button._cifereca_action_id = "choose-normal-lender"
    button.connect("clicked", lambda control: present_normal_lender_choice(
        panel, item=item, rows=rows, current=current, service=service,
        identity=identity, identity_id=identity_id, account_epoch=account_epoch,
        source_epoch=source_epoch, Adw=Adw, TaskPriority=TaskPriority, control=control,
    ))
    panel._actions([button])
    return True
