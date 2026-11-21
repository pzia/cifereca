"""#6397: exact public or authenticated C web actions in standard detail.

Read choices and resolve the selected opaque id on the existing task runner,
never on GTK. The caller supplies its original selection/identity/epoch
guard; a disposed or superseded detail cannot publish or open a browser.
No provider request, generic fallback, session transfer or acquisition.
"""
from __future__ import annotations

from ..core.notice_c_cover_index import is_canonical_cover_document
from ..core.notice_c_web_choices import (
    PublicWebChoices, read_public_web_choices, resolve_public_web_choice,
)
from ..storage import Storage
from ..core.notice_c_authenticated_web_choices import (
    AuthenticatedWebChoices, resolve_authenticated_web_choice,
)
from ..core.notice_c_private_search_web import read_authenticated_web_for_search
from ..core.search_orchestration import SearchReadScope, capture_search_read_scope
from ..models import SearchResult


def request_public_web_actions(host, panel, document, *, expected_epoch, current,
                               Gtk=None, Gio=None):
    # Match the exact integer epoch contract of the Core click resolver.
    # Python bool and float equality must not authorize a stale menu.
    if (not is_canonical_cover_document(document)
            or type(expected_epoch) is not int or not 1 <= expected_epoch < 2**63
            or not current()):
        return False
    return _request_web_actions(
        host, panel, current=current, Gtk=Gtk, Gio=Gio,
        load=lambda core, storage: read_public_web_choices(storage, document),
        accept=lambda menu: (type(menu) is PublicWebChoices
                             and menu.document == document
                             and type(menu.epoch) is int and menu.epoch == expected_epoch),
        resolve=lambda core, storage, menu, choice: resolve_public_web_choice(
            storage, document, expected_epoch=menu.epoch, choice_id=choice.choice_id),
    )


def request_authenticated_search_web_actions(host, panel, row, *, reader_scope, current,
                                             source_current=lambda: True, Gtk=None, Gio=None):
    """Same menu lifecycle, exact private self-C read and explicit click only."""
    core = getattr(host, "service", None)
    if (type(row) is not SearchResult or type(reader_scope) is not SearchReadScope
            or not reader_scope.current(core) or not current()):
        return False
    def scoped_current():
        return reader_scope.current(core) and source_current()

    def resolve(core, storage, menu, choice):
        # The selected row may have changed its coordinates while the menu
        # was displayed. Match the fresh exact self-C, never the row URL.
        fresh = read_authenticated_web_for_search(core, row, reader_scope=reader_scope)
        if (type(fresh) is not AuthenticatedWebChoices or fresh.document != menu.document
                or fresh.epoch != menu.epoch or fresh.choices != menu.choices):
            return None
        return resolve_authenticated_web_choice(core, menu, choice_id=choice.choice_id)

    return _request_web_actions(
        host, panel, current=current, Gtk=Gtk, Gio=Gio, authenticated=True,
        worker_guard=scoped_current,
        load=lambda core, storage: read_authenticated_web_for_search(
            core, row, reader_scope=reader_scope),
        accept=lambda menu: (type(menu) is AuthenticatedWebChoices
                             and menu._scope is reader_scope),
        resolve=resolve,
    )


def request_selected_authenticated_web_actions(host, row, *, selected_id, context):
    """Capture the exact selected GTK row and publication lease, not its ID alone."""
    if (type(row) is not SearchResult or context is None
            or not row.source_credential or row.source_credential == "__public__"
            or not row.source_account_id):
        return False
    core = host.service
    panel = host.search_page.detail
    selection = getattr(host.search_page, "selection", None)
    get_selected = getattr(selection, "get_selected_item", None)
    if not callable(get_selected):
        return False
    cell = get_selected()
    key = getattr(cell, "key", None)
    if type(key) is not str:
        return False
    try:
        scope = capture_search_read_scope(core, context[3])
    except (AttributeError, TypeError, ValueError, IndexError):
        return False
    token = object()
    panel._authenticated_web_request_token = token

    def source_current():
        objects = getattr(host, "search_objects", None)
        return (type(objects) is dict and objects.get(key) is row
                and getattr(panel, "_authenticated_web_request_token", None) is token)

    def current():
        return (source_current() and host.search_page.detail is panel
                and get_selected() is cell
                and host._search_detail_context_current(selected_id, context))

    return request_authenticated_search_web_actions(
        host, panel, row, reader_scope=scope, current=current,
        source_current=source_current,
    )


def _request_web_actions(host, panel, *, current, load, accept, resolve,
                         authenticated=False, worker_guard=lambda: True, Gtk=None, Gio=None):
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
                and route_token() is route and worker_guard())

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
                or not accept(menu) or not menu.choices):
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
                return resolve(core, storage, menu, choice)

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
            root = Gtk.Button(label=("Ouvrir la fiche web · Connexion requise"
                                     if authenticated else "Ouvrir la fiche web"))
            root.connect("clicked", lambda button: choose(button, menu.choices[0]))
        else:
            root = Gtk.MenuButton(label=("Fiches web · Connexion requise…"
                                         if authenticated else "Ouvrir la fiche sur…"))
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
        root._cifereca_action_id = ("open-c-authenticated-web" if authenticated
                                     else "open-c-public-web")
        root._cifereca_action_priority = 30
        panel.add_actions([root])

    def read_menu():
        if not initial_pending or not worker_current(requested_route):
            return None
        return load(core, storage)

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
