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
from ..core.notice_c_private_search_web import read_authenticated_web_for_search, _source_key
from ..core.loan_action_scope import _inert_session_state, _inert_session_accounts
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
    # Bind native coordinates before enqueue. The mutable DTO may change
    # in place after a worker read but before the GTK callback is delivered.
    def coordinates():
        return (_source_key(row, reader_scope.identity_id), row.id, row.scenario)

    captured = coordinates()
    if captured[0] is None:
        return False

    def scoped_current():
        return (reader_scope.current(core) and source_current()
                and coordinates() == captured)

    def display_current(menu):
        # Inert snapshots only: no SQLite, provider dispatch or login from
        # GTK. Core still performs the full durable revalidation at click.
        try:
            for binding in menu._bindings:
                doc = binding.document
                state = _inert_session_state(binding.session)
                accounts = _inert_session_accounts(binding.session)
                if (core._sessions.get(core._session_key(doc.provider_id, doc.credential_id))
                        is not binding.session or state is None or accounts is None
                        or state.get('connected') is not True or state.get('_closed') is True
                        or state.get('descriptor') is not binding.descriptor
                        or state.get('provider_id') != doc.provider_id
                        or state.get('credential_id') != doc.credential_id
                        or type(state.get('_epoch')) is not type(binding.session_epoch)
                        or state.get('_epoch') != binding.session_epoch
                        or sum(a is binding.account for a in accounts) != 1
                        or (binding.account.identity_id, binding.account.provider_id,
                            binding.account.credential_id, binding.account.unique_id)
                        != (doc.identity_id, doc.provider_id, doc.credential_id, doc.account_id)):
                    return False
            return bool(menu._bindings)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return False

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
        worker_guard=scoped_current, menu_current=display_current,
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
        return type(objects) is dict and objects.get(key) is row

    def current():
        return (source_current() and host.search_page.detail is panel
                and getattr(panel, "_authenticated_web_request_token", None) is token
                and get_selected() is cell
                and host._search_detail_context_current(selected_id, context))

    return request_authenticated_search_web_actions(
        host, panel, row, reader_scope=scope, current=current,
        source_current=source_current,
    )


def _web_choice_labels(core, choices):
    """Human library names with exact source distinctions, no new authority.

    Resolve the installed display name locally, once per provider. The
    original opaque choices still own resolution and are never rewritten.
    """
    from collections import Counter

    lookup = getattr(core, "provider_display_name", None)
    names = {}
    labels = []
    for choice in choices:
        provider = choice.provider_id
        if provider not in names:
            try:
                name = lookup(provider) if callable(lookup) else None
            except Exception:
                name = None
            names[provider] = (
                name if type(name) is str and 0 < len(name) <= 80
                and name == name.strip() and name.isprintable()
                and not any(mark in name for mark in ("://", "@", "/", chr(92)))
                else provider
            )
        label = choice.label
        # A provider name may prefix an unrelated human-readable label
        # (e.g. "paris" and "parisian"). Only rewrite a whole provider
        # component, never the beginning of a different name.
        if label == provider or (label.startswith(provider)
                                 and label[len(provider):len(provider) + 1]
                                 in {" ", "·", "(", ":"}):
            label = names[provider] + label[len(provider):]
        labels.append(label)
    counts = Counter(labels)
    # Installed descriptors can have the same label. Preserve the exact
    # distinctions even when two providers share a human-readable name.
    labels = [
        label + " · " + choice.provider_id if counts[label] > 1 else label
        for label, choice in zip(labels, choices)
    ]
    if len(set(labels)) != len(labels):
        labels = [f"Choix {i}/{len(labels)} · {label}"
                  for i, label in enumerate(labels, 1)]
    return tuple(labels)


def _request_web_actions(host, panel, *, current, load, accept, resolve,
                         authenticated=False, worker_guard=lambda: True,
                         menu_current=lambda menu: True, Gtk=None, Gio=None):
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
    # Public and authenticated menus have separate publication leases.
    # A newer request for the same kind revokes an older queued read/click
    # even if the panel and route are reused for the next detail.
    token_name = ("_notice_c_authenticated_menu_generation" if authenticated
                  else "_notice_c_public_menu_generation")
    generation = object()
    setattr(panel, token_name, generation)
    account_epoch = getattr(core, "_account_context_epoch", None)
    source_epoch = getattr(core, "_source_context_epoch", None)

    def worker_current(route):
        # Only plain context coordinates here. current(), get_mapped()
        # and widget state belong to GTK and must never run in this worker.
        return (not getattr(host, "_closed", False)
                and getattr(panel, token_name, None) is generation
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
                or not accept(menu) or not menu.choices or not menu_current(menu)):
            return
        root = None
        pending_click = None

        def root_current():
            return (active() and menu_current(menu) and root is not None
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
            for choice, label in zip(menu.choices, _web_choice_labels(core, menu.choices)):
                button = Gtk.Button(label=label)
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
