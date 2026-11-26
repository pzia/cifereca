"""#4988: explicit consent for the normal lender path, without copy rights."""
from __future__ import annotations

from threading import Lock

from ..core.borrow_for_identity_discovery import (
    possible_lender_matches, possible_lender_session_matches,
    possible_lender_claims_distinct,
)


def present_normal_lender_choice(panel, *, item, rows, current, service,
                                 identity, identity_id, account_epoch,
                                 source_epoch, Adw, TaskPriority, control=None,
                                 native_roster=None, canonical_binding=None):
    """Choose among multiple candidates, then require explicit confirmation."""
    window = panel.window
    execute = getattr(service, "execute_normal_lender_borrow_for_current_identity", None)

    def lender_current(row):
        if native_roster is not None and row is None:
            return False
        return (possible_lender_matches(
            row, row._notice if native_roster is not None else item,
            requesting_identity_id=identity_id,
        ) and (row._session is None or possible_lender_session_matches(
            row, row._session,
        )))
    def roster_current():
        # The original N-way receipt remains indivisible through consent
        # and a queued worker, not only while the chooser is displayed.
        if native_roster is not None:
            from ..core.borrow_for_identity_native_roster import (
                native_roster_matches_detail, native_roster_matches_document,
            )
            matches = (native_roster_matches_document(
                service, native_roster, canonical_binding[0],
                expected_epoch=canonical_binding[1], requesting_identity_id=identity_id,
            ) if canonical_binding is not None else native_roster_matches_detail(
                service, native_roster, item, requesting_identity_id=identity_id,
            ))
            return native_roster.rows is rows and matches and all(lender_current(row) for row in rows)
        return (possible_lender_claims_distinct(
                    rows, item, requesting_identity_id=identity_id)
                and all(lender_current(row) for row in rows))

    if (not callable(execute) or not current()
            or type(rows) is not tuple or not 1 <= len(rows) <= 64
            or len({id(row) for row in rows}) != len(rows)
            or not roster_current()
            or getattr(window, "_cross_profile_borrow_pending", None) is not None):
        return False
    token = object()
    window._cross_profile_borrow_pending = token
    stage = "choose"
    route = getattr(window, "_route_display_token", None)
    visible = getattr(panel, "_action_buttons" if canonical_binding is not None
                      else "_rendered_search_detail", None)
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
                and getattr(panel, "_action_buttons" if canonical_binding is not None
                            else "_rendered_search_detail", None) is visible
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
                or not lender_current(candidate) or not roster_current()):
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
            if (not route_current() or not lender_current(candidate)
                    or not roster_current()):
                return {"status": "blocked"}
            return execute(
                captured_candidate=candidate,
                exact_notice=candidate._notice if native_roster is not None else item,
                expected_requesting_identity_id=identity_id,
                expected_requesting_identity=identity,
                expected_context_epoch=account_epoch,
                expected_source_epoch=source_epoch, confirmed=True,
                captured_roster=native_roster if native_roster is not None else rows,
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
        if not dialog_current() or not lender_current(candidate) or not roster_current():
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
            # Constructing/connecting a native GTK dialog can re-enter the
            # detail route and even rewrite a lender in place. Never paint
            # confirmation for a revoked account/edition.
            if not dialog_current() or not lender_current(candidate) or not roster_current():
                release()
                return False
            menu.present()
            return True
        except Exception:
            release()
            return False

    if len(rows) == 1:
        return present_confirmation(rows[0])

    choices = {f"choice-{i}": row for i, row in enumerate(rows)}

    def selected(_dialog, response):
        # The displayed N-way inventory is a single exact choice receipt.
        # An unselected lender can change after painting without changing
        # the selected candidate. Do not turn a stale N-way roster into a
        # seemingly valid one-account confirmation.
        if (not dialog_current()
                or not roster_current()):
            release()
            return
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
        # Do not display an obsolete N-way choice if GTK construction
        # changed the original borrower, provider claim or source notice.
        if (not dialog_current()
                or not roster_current()):
            release()
            return False
        menu.present()
        return True
    except Exception:
        release()
        return False


def append_normal_lender_action(panel, *, item, rows, visible, service,
                                identity, identity_id, account_epoch, source_epoch, route,
                                native_roster=None, canonical_binding=None, selection_current=None):
    """Attach an optional consent entry to the existing shared detail action bar."""
    window = panel.window

    def roster_current():
        if native_roster is not None:
            from ..core.borrow_for_identity_native_roster import (
                native_roster_matches_detail, native_roster_matches_document,
            )
            matches = (native_roster_matches_document(
                service, native_roster, canonical_binding[0],
                expected_epoch=canonical_binding[1], requesting_identity_id=identity_id,
            ) if canonical_binding is not None else native_roster_matches_detail(
                service, native_roster, item, requesting_identity_id=identity_id,
            ))
            return native_roster.rows is rows and matches
        return possible_lender_claims_distinct(rows, item, requesting_identity_id=identity_id)

    if (getattr(window, "_route_display_token", None) is not route
            or type(rows) is not tuple or not 1 <= len(rows) <= 64
            or len({id(row) for row in rows}) != len(rows)
            or not roster_current()
            or (canonical_binding is not None and
                (native_roster is None or not callable(selection_current) or not selection_current()))
            or not callable(getattr(panel, "_actions", None))
            or not callable(getattr(service, "execute_normal_lender_borrow_for_current_identity", None))
            or any(not possible_lender_matches(
                row, row._notice if native_roster is not None else item,
                requesting_identity_id=identity_id,
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
                    and (getattr(panel, "_action_buttons", None) is visible and selection_current()
                         if canonical_binding is not None else panel._rendered_search_detail is visible)
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
    pending = False

    def clicked(control):
        nonlocal pending

        def present():
            return present_normal_lender_choice(
                panel, item=item, rows=rows, current=current, service=service,
                identity=identity, identity_id=identity_id, account_epoch=account_epoch,
                source_epoch=source_epoch, Adw=Adw, TaskPriority=TaskPriority, control=control,
                **({"native_roster": native_roster} if native_roster is not None else {}),
                **({"canonical_binding": canonical_binding} if canonical_binding is not None else {}),
            )

        if native_roster is None:
            return present()
        if (pending or not current() or not roster_current()
                or getattr(window, "_cross_profile_borrow_pending", None) is not None):
            return False
        from ..core.borrow_for_identity_native_roster import native_lender_roster_current
        pending = True

        def work():
            return roster_current() and native_lender_roster_current(service, native_roster)

        def delivered(valid):
            nonlocal pending
            if not pending:
                return
            pending = False
            if valid is True and current() and roster_current():
                present()

        def failed(_error):
            delivered(False)

        try:
            window.runner.submit("Vérification des sources prêteuses…", work, delivered, failed,
                                 priority=TaskPriority.USER_ACTION, kind="detail-cache",
                                 origin="user")
            return True
        except Exception:
            failed(None)
            return False

    button.connect("clicked", clicked)
    # Creating a GTK control can re-enter navigation or change a live
    # lender/notice before the action is attached. Reject a stale widget
    # BEFORE painting it, not only when the user eventually clicks.
    if (not current() or not roster_current()
            or any(row._session is not None and not possible_lender_session_matches(
                row, row._session) for row in rows)):
        return False
    panel._actions([button])
    return True
