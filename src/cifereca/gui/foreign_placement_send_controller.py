"""#4988: copy an already-present A placement to current B, with fresh consent.

This is the independent local 'Envoyer à' path, NOT a borrow action. It
neither observes nor modifies supplier entitlements. The Core executor
rechecks source ownership, physical bytes and destination under its lock.
"""
from __future__ import annotations


def add_foreign_present_placement_send(
    window, detail, item, *, TaskPriority, dialog_factory=None,
) -> bool:
    """Show an explicit send option for a foreign, exact local placement.

    The action is deliberately advisory until the user confirms. A frozen
    row or a GTK button never becomes execution authority.
    """
    if getattr(window, "_closed", False) or type(item) is not dict:
        return False
    service = window.service
    action = getattr(service, "send_present_placement_copy", None)
    if not callable(action):
        return False
    source_placement = item.get("placement_id")
    source_identity = item.get("identity_id")
    source_artifact = item.get("artifact_id")
    try:
        requester = service.current_identity
        target_id = requester.identity_id
        account_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        ui_identity_epoch = window._identity_context_generation
        ui_account_epoch = window._account_context_generation
        # A Library view-scope toggle is a separate cancellation boundary:
        # B may remain selected while A's old detail is still on screen.
        library_scope_generation = getattr(
            window, "_library_scope_generation", None,
        )
    except Exception:
        return False
    if (any(type(x) is not str or not 0 < len(x) <= 256
            or not x.isprintable() or x != x.strip()
            for x in (source_placement, source_identity,
                      source_artifact, target_id))
            or source_identity == target_id
            or type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0
            or type(ui_identity_epoch) is not int
            or type(ui_account_epoch) is not int):
        return False
    token = object()
    button = window.action_button(
        "Envoyer une copie à mon profil…", "document-send-symbolic",
    )
    button._cifereca_action_id = "send-present-foreign-placement"
    button.set_tooltip_text(
        "Copie indépendante du fichier déjà présent sur un autre profil. "
        "Aucun emprunt ni droit fournisseur n'est transféré."
    )
    state = {"phase": "idle"}

    def live(*, mapped=False):
        try:
            if (getattr(window, "_closed", False)
                    or window.service is not service
                    or service.current_identity is not requester
                    or service.current_identity.identity_id != target_id
                    or service._account_context_epoch != account_epoch
                    or service._source_context_epoch != source_epoch
                    or window._identity_context_generation != ui_identity_epoch
                    or window._account_context_generation != ui_account_epoch
                    or getattr(window, "_library_scope_generation", None)
                       != library_scope_generation
                    or getattr(button, "_cifereca_action_context_revoked", False)):
                return False
            if mapped:
                root = getattr(button, "get_root", None)
                is_mapped = getattr(button, "get_mapped", None)
                if (callable(root) and root() is None
                        or callable(is_mapped) and is_mapped() is False):
                    return False
            return True
        except Exception:
            return False

    def release():
        state["phase"] = "done"
        if getattr(window, "_foreign_placement_send_pending", None) is token:
            window._foreign_placement_send_pending = None

    def accepted(_dialog, response):
        if state["phase"] != "confirm":
            return
        if response != "send" or not live():
            release()
            return
        state["phase"] = "working"

        def work():
            if (state["phase"] != "working"
                    or getattr(window, "_foreign_placement_send_pending", None)
                       is not token
                    or not live()):
                return {"status": "blocked"}
            return action(
                source_placement_id=source_placement,
                source_identity_id=source_identity,
                source_artifact_id=source_artifact,
                requesting_identity_id=target_id,
                expected_account_context_epoch=account_epoch,
                expected_source_context_epoch=source_epoch,
                confirmed=True,
            )

        def completed(result):
            valid = live()
            release()
            if not valid:
                return
            if (type(result) is dict
                    and result.get("status") == "complete"
                    and result.get("loan") == "not_attempted"
                    and result.get("delivery") == "present"
                    and result.get("transfer") == "completed"
                    and result.get("reason_code") ==
                        "independent_local_copy_completed"
                    and result.get("retry_borrow") is False):
                window._activity_result(
                    "Copie indépendante envoyée vers votre profil."
                )
                window.refresh_library(False)
                window.refresh_recent()
            else:
                window.show_error(
                    "Copie locale non confirmée. Vérifiez que le livre "
                    "est toujours présent sur le profil d'origine.",
                    "Envoi entre profils",
                )

        def failed(_exception):
            valid = live()
            release()
            if valid:
                window.show_error(
                    "Copie locale indisponible ou contexte modifié.",
                    "Envoi entre profils",
                )

        try:
            window.runner.submit(
                "Copie locale entre profils…", work, completed, failed,
                control=button, priority=TaskPriority.USER_ACTION,
                kind="transfer",
            )
        except Exception:
            release()
            if live():
                window.show_error(
                    "Envoi local indisponible.", "Envoi entre profils",
                )

    def clicked(_button):
        if state["phase"] != "idle" or not live(mapped=True):
            return
        if getattr(window, "_foreign_placement_send_pending", None) is not None:
            return
        window._foreign_placement_send_pending = token
        state["phase"] = "confirm"
        try:
            if dialog_factory is None:
                from gi.repository import Adw
                dialog = Adw.MessageDialog.new(
                    window, "Envoyer une copie vers votre profil ?",
                    "Un fichier est présent sur un autre profil. "
                    "Créer une copie indépendante via « Envoyer à » ? "
                    "Aucun emprunt ni droit fournisseur n'est transféré.",
                )
            else:
                dialog = dialog_factory(window)
            dialog.add_response("cancel", "Annuler")
            dialog.add_response("send", "Envoyer une copie")
            dialog.connect("response", accepted)
            if not live():
                release()
                return
            dialog.present()
        except Exception:
            release()

    button.connect("clicked", clicked)
    if not live():
        return False
    detail.add_actions([button])
    return True


__all__ = ["add_foreign_present_placement_send"]
