"""#6397: one exact public C web action in the current standard detail.

Read choices and resolve the selected opaque id on the existing task runner,
never on GTK. The caller supplies its original selection/identity/epoch
guard; a disposed or superseded detail cannot publish or open a browser.
No provider request, generic fallback, private session or acquisition.
"""
from __future__ import annotations

from ..core.notice_c_cover_index import is_canonical_cover_document
from ..core.notice_c_web_choices import (
    PublicWebChoices, read_public_web_choices, resolve_public_web_choice,
)
from ..storage import Storage


def request_public_web_actions(host, panel, document, *, expected_epoch, current,
                               Gtk=None, Gio=None):
    if not is_canonical_cover_document(document) or not current():
        return False
    core = getattr(host, "service", None)
    storage = getattr(core, "storage", None)
    runner = getattr(host, "runner", None)
    if (not isinstance(storage, Storage) or not callable(getattr(runner, "submit", None))
            or not callable(getattr(panel, "add_actions", None))):
        return False
    if Gtk is None:
        from gi.repository import Gtk
    if Gio is None:
        from gi.repository import Gio
    from ..task_queue import TaskPriority

    def route_token():
        return getattr(host, "_route_display_token", None)

    requested_route = route_token()
    account_epoch = getattr(core, "_account_context_epoch", None)
    source_epoch = getattr(core, "_source_context_epoch", None)

    def worker_current(route):
        # Only plain context coordinates here. current(), get_mapped()
        # and widget state belong to GTK and must never run in this worker.
        return (not getattr(host, "_closed", False)
                and getattr(host, "service", None) is core
                and getattr(core, "storage", None) is storage
                and getattr(core, "_account_context_epoch", None) == account_epoch
                and getattr(core, "_source_context_epoch", None) == source_epoch
                and route_token() is route)

    mapped = getattr(panel, "get_mapped", None)

    def active():
        return (worker_current(route_token())
                and (not callable(mapped) or mapped())
                and current())

    initial_pending = True

    def consume_initial():
        nonlocal initial_pending
        if not initial_pending:
            return False
        initial_pending = False
        return True

    def load_failed(_error):
        consume_initial()

    def loaded(menu):
        if not consume_initial():
            return
        if (not active() or route_token() is not requested_route
                or type(menu) is not PublicWebChoices
                or menu.document != document or menu.epoch != expected_epoch
                or not menu.choices):
            return
        root = None
        pending_click = None

        def root_current():
            return (active() and root is not None
                    and not getattr(root, "_cifereca_action_context_revoked", False))

        def choose(_button, choice):
            nonlocal pending_click
            if pending_click is not None or not root_current():
                return
            token = object()
            pending_click = token
            clicked_route = route_token()

            def click_current():
                return root_current() and route_token() is clicked_route

            def consume():
                nonlocal pending_click
                if pending_click is not token:
                    return False
                pending_click = None
                return True

            def failed(_exc):
                if consume() and click_current():
                    host.toast("Fiche web indisponible ; actualisez la notice.")

            def resolved(uri):
                # Only the one currently pending click can consume its
                # completion. A duplicate/late callback cannot clear a
                # newer click or launch the browser a second time.
                if not consume() or not click_current():
                    return
                if not uri:
                    host.toast("Fiche web modifiée ; actualisez la notice.")
                    return
                try:
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                except Exception:
                    host.toast("Ouverture de la fiche web indisponible.")

            def work():
                if pending_click is not token or not worker_current(clicked_route):
                    return None
                return resolve_public_web_choice(
                    storage, document, expected_epoch=menu.epoch,
                    choice_id=choice.choice_id,
                )

            try:
                runner.submit(
                    "Vérification de la fiche web…",
                    work,
                    resolved, failed, priority=TaskPriority.METADATA,
                    kind="detail-cache", origin="user",
                )
            except Exception:
                failed(None)

        if len(menu.choices) == 1:
            root = Gtk.Button(label="Ouvrir la fiche web")
            root.connect("clicked", lambda button: choose(button, menu.choices[0]))
        else:
            root = Gtk.MenuButton(label="Ouvrir la fiche sur…")
            popover = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            # At most 32 sources × 8 references. Keep the bounded menu
            # scrollable, without removing eligible options.
            scroller = Gtk.ScrolledWindow()
            scroller.set_max_content_height(360)
            scroller.set_propagate_natural_height(True)
            scroller.set_child(box)
            for choice in menu.choices:
                button = Gtk.Button(label=choice.label)
                button.connect("clicked", lambda b, selected=choice: choose(b, selected))
                box.append(button)
            popover.set_child(scroller)
            root.set_popover(popover)
        root._cifereca_action_id = "open-c-public-web"
        root._cifereca_action_priority = 30
        panel.add_actions([root])

    def read_menu():
        if not initial_pending or not worker_current(requested_route):
            return None
        return read_public_web_choices(storage, document)

    try:
        runner.submit(
            "Lecture des fiches web en cache…",
            read_menu,
            loaded, load_failed, priority=TaskPriority.METADATA,
            kind="detail-cache", origin="automatic",
        )
    except Exception:
        consume_initial()
        return False
    return True
