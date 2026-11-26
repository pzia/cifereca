"""Search page and task adapter using the host's per-window search state.

The compatibility host keeps identity ownership and the TaskRunner. The
existing controller bodies retain their explicit network policy, generation
checks, pending-request token and GLib main-thread batch delivery.
"""
from __future__ import annotations

from .search_host_port import SearchControllerHost

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from .master_detail import MasterDetailPage
from .query_model import progressive_online_search, merge_results
from .search_response_facets import SearchResponseFacets
from .task_runner import TaskPriority


def _reset_search_enter(self: SearchControllerHost):
    sequence = getattr(self, "_search_enter_sequence", None)
    if sequence is not None:
        sequence.reset()


def _search_controls_changed(self: SearchControllerHost):
    if getattr(self, "_closed", False):
        return
    self._reset_search_enter()
    self._render_search_results()


def _search_enter(self: SearchControllerHost):
    if getattr(self, "_closed", False):
        return
    from .search_intent import SearchEnterSequence
    from .search_pending import pending_online_search_matches
    if not hasattr(self, "_search_enter_sequence"):
        self._search_enter_sequence = SearchEnterSequence()
    state = self.search_controls.state()
    identity = self.service.current_identity.identity_id if self.service.current_identity else None
    if pending_online_search_matches(
        getattr(self, "_online_pending_key", None), (state, identity),
        getattr(self, "_online_pending_generation", None),
        getattr(self, "_search_generation", 0),
    ):
        return
    online = self._search_enter_sequence.press(state)
    self.run_search(online, keyboard=True)


def _search_key_interaction(self: SearchControllerHost, _controller, keyval, _keycode, _state):
    if keyval not in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
        self._reset_search_enter()
    return False


def _build_search_page(self: SearchControllerHost) -> MasterDetailPage:
    page = MasterDetailPage(self, "Recherche", "", "Lancez une recherche", "Recherche dans le connu, puis extension aux catalogues en ligne sur demande.")
    # Search also has a response-facets control next to the title/count.
    # Keeping its page actions on that same horizontal row forces GTK's
    # natural minimum beyond the physical 1024px Xvfb viewport (1111px
    # page minimum observed in #774). Put the unchanged action bar on its
    # own row, above the query controls, without hiding any action.
    page.page_header.remove(page.header_actions)
    page.insert_child_after(page.header_actions, page.page_header)
    page.header_actions.source = page
    self.search_controls = self._query_controls(self._search_controls_changed, self._search_enter)
    page.toolbar.append(self.search_controls)
    self.search_entry = self.search_controls.entry
    if not hasattr(self, "_search_interaction_controllers"):
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._search_key_interaction)
        self.add_controller(keys)
        clicks = Gtk.GestureClick()
        clicks.set_button(0)
        clicks.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clicks.connect("pressed", lambda *_: self._reset_search_enter())
        self.add_controller(clicks)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", lambda *_: (self._reset_search_enter(), False)[1])
        self.add_controller(scroll)
        self._search_interaction_controllers = (keys, clicks, scroll)
    self.search_button = Gtk.Button(label="Recherche"); self.search_button.add_css_class("suggested-action")
    self.search_button.connect("clicked", lambda _b: self.run_search(False)); self.search_controls.primary.append(self.search_button)
    self.search_global_button = Gtk.Button(label="Étendre en ligne")
    self.search_global_button.set_tooltip_text("Interroger les catalogues distants avec la requête et les filtres actuels")
    self.search_global_button.connect("clicked", lambda _b: self.run_search(True)); self.search_controls.primary.append(self.search_global_button)
    self._search_response_facets = SearchResponseFacets()
    page.page_header.insert_child_after(self._search_response_facets, page.count_label)
    page.connect_selected(self._search_selected)
    self._search_generation = getattr(self, "_search_generation", 0) + 1
    self._remote_search_results = []
    self._online_source_statuses = {}
    self._online_source_context = None
    return page


def search_books(
    self: SearchControllerHost, query: str, *, field: str = "title",
    online: bool = False, identity_id: str | None = None,
    providers: tuple[str, ...] = (),
) -> bool:
    """Search from a trusted book detail; exactly one explicit query.

    This API is public through UiSearchNavigationHost. No credentials,
    selected identity or old filters are copied into a new search.
    """
    # A plugin callback may arrive after its UI host/window has been closed.
    # Reject before touching stale GTK controls or triggering navigation.
    if getattr(self, "_closed", False):
        return False
    from .query_model import FIELDS, QueryState

    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 512:
        return False
    if field not in FIELDS or type(online) is not bool:
        return False
    current = getattr(self.service, "current_identity", None)
    current_id = getattr(current, "identity_id", None)
    if identity_id is not None and (not current_id or identity_id != current_id):
        return False
    controls = getattr(self, "search_controls", None)
    if controls is None:
        return False
    # A plugin may request an explicit known source; never allow an
    # unconfigured/disabled provider, a stale identity, or a raw credential
    # from a merged recommendation to select a remote endpoint.
    if (type(providers) is not tuple or len(providers) > 4
            or any(type(value) is not str for value in providers)
            or len(set(providers)) != len(providers)
            or "local" in providers):
        return False
    if providers:
        try:
            active = {
                provider for provider, _label in self.service.searchable_providers()
                if type(provider) is str
            }
        except Exception:
            return False
        if not set(providers) <= active:
            return False
    controls.set_state(
        QueryState(
            text=query.strip(), field=field, scope="all", providers=providers,
        ), notify=False,
    )
    self.navigate("search")
    # Local by default for #906; online only on an explicit loupe click.
    self.run_search(online)
    return True


def run_search(self: SearchControllerHost, force: bool = False, *, keyboard: bool = False) -> None:
    if getattr(self, "_closed", False):
        return
    from .search_pending import pending_online_search_matches
    if not keyboard:
        self._reset_search_enter()
    state = self.search_controls.state()
    if len(state.text) < 2:
        self.toast("Saisissez au moins 2 caractères"); return
    # One Core and account/source snapshot owns the submitted search. GTK
    # navigation must never choose another database/provider after enqueue.
    request_core = self.service
    admitted_identity = getattr(request_core, "current_identity", None)
    request_identity = getattr(admitted_identity, "identity_id", None)
    admitted_account_epoch = getattr(request_core, "_account_context_epoch", None)
    admitted_source_epoch = getattr(request_core, "_source_context_epoch", None)
    online_key = (state, request_identity)
    if force and pending_online_search_matches(
        getattr(self, "_online_pending_key", None), online_key,
        getattr(self, "_online_pending_generation", None),
        self._search_generation,
    ):
        return
    request_scope = None
    if force:
        from ..core.search_orchestration import capture_search_read_scope
        try:
            request_scope = capture_search_read_scope(request_core, request_identity)
        except ValueError:
            self.toast("Contexte lecteur indisponible")
            return
    identity_generation = getattr(self, "_identity_context_generation", None)
    account_generation = getattr(self, "_account_context_generation", None)
    self._search_generation += 1
    # A previous Search result must not remain navigable from a plugin while
    # the next request is pending, even if both use the same query text.
    self._loaded_search_context = None
    generation = self._search_generation
    identity_id = request_identity
    # Never reuse source verdicts across requests, accounts or generations.
    self._online_source_statuses = {}
    self._online_search_warnings = ()
    self._online_source_context = (
        (state.text, state.field, state.scope, identity_id) if force else None
    )
    from cifereca.gui.search_document_cover_bridge import search_cover_context
    self._online_cover_context = search_cover_context(self) if force else None
    response_facets = getattr(self, "_search_response_facets", None)
    if response_facets is not None:
        response_facets.begin(self._online_source_context)
    def admitted_core_current():
        # Navigation only controls GTK delivery, never the admitted worker.
        return (
            getattr(request_core, "current_identity", None) is admitted_identity
            and getattr(request_core, "_account_context_epoch", None) == admitted_account_epoch
            and getattr(request_core, "_source_context_epoch", None) == admitted_source_epoch
        )
    def current():
        if (getattr(self, "_closed", False)
                or self.service is not request_core
                or not admitted_core_current()
                or getattr(self, "_identity_context_generation", None) != identity_generation
                or getattr(self, "_account_context_generation", None) != account_generation
                or (request_scope is not None and not request_scope.current(request_core))):
            return False
        identity = getattr(getattr(request_core, "current_identity", None), "identity_id", None)
        live_state = self.search_controls.state()
        # Editing the query text/field/scope invalidates in-flight results
        # immediately, even before the next explicit search. Local facets
        # (provider/type/language) can still re-filter already-loaded rows.
        return (
            generation == self._search_generation
            and identity_id == identity
            and (live_state.text, live_state.field, live_state.scope) ==
                (state.text, state.field, state.scope)
        )
    if not force:
        self._last_search_query = state.text
        self._search_mode = "local"
        # The SQLite worker belongs to the Core active at enqueue, not to a
        # later MainWindow service (which may point to a different database).
        known_core = self.service
        identity_generation = getattr(self, "_identity_context_generation", None)
        account_generation = getattr(self, "_account_context_generation", None)
        def done(rows):
            # Core execution remains valid after navigation; GTK delivery does
            # not, including A→B→A with the same visible identity label.
            if (current() and self.service is known_core
                    and getattr(self, "_identity_context_generation", None) == identity_generation
                    and getattr(self, "_account_context_generation", None) == account_generation):
                self._populate_local_search(rows)
        self.runner.submit("Recherche dans le connu…",
            lambda: known_core.search_known_catalog(state.text, state.scope, field=state.field, identity_id=identity_id),
            done, control=self.search_button, priority=TaskPriority.USER_ACTION, kind="search-known")
        return
    # A NEW explicit online request is a new source/account snapshot even
    # when the text and local facets are unchanged. Merging its batches with
    # earlier rows could retain a Paris edition from the former primary
    # credential (or a now-disconnected reader). Local facet changes remain
    # reversible without re-querying, using this request's loaded rows only.
    request_key = (state.text, state.field, state.scope)
    had_remote_rows = bool(self._remote_search_results)
    previous_known = getattr(self, "_search_local_objects", None)
    had_c_preview = type(previous_known) is dict and any(
        type(row) is dict and row.get("source") == "notice_c_public"
        for row in previous_known.values()
    )
    if request_key != getattr(self, "_remote_query_key", None):
        if state.text != self._last_search_query: self._search_local_objects = {}
    # A durable C is not an evergreen receipt of membership in a query.
    # Every new Search must re-read the C cursor before republishing it.
    if had_c_preview and type(self._search_local_objects) is dict:
        self._search_local_objects = {
            key: row for key, row in self._search_local_objects.items()
            if type(row) is not dict or row.get("source") != "notice_c_public"
        }
    self._remote_search_results = []
    self._remote_query_key = request_key
    if had_remote_rows or had_c_preview:
        # Revoke old GTK row/detail actions synchronously, before any worker
        # completes. Late batches are independently rejected by generation.
        self._populate_search([], announce=False)
    self._last_search_query = state.text
    pending_token = object()
    self._online_pending_key = online_key
    self._online_pending_token = pending_token
    self._online_pending_generation = generation
    def clear_pending():
        if getattr(self, "_online_pending_token", None) is pending_token:
            self._online_pending_key = None
            self._online_pending_generation = None
    def failed(exc):
        clear_pending()
        if current():
            # The provider exception may contain a signed URL, a token or
            # identity-specific details. UI errors must not echo raw input.
            self.show_error(
                "Les catalogues en ligne ne répondent pas. Réessayez ultérieurement.",
                "Recherche en ligne impossible",
            )
    def deliver_batch(batch):
        if not current(): return False
        if response_facets is not None:
            response_facets.receive(batch)
        from .search_batch_feedback import public_batch_status
        # Closed Core status and bounded provider key. The ID is only a
        # per-request map key; neither ID nor label enters the GUI caption.
        provider = getattr(batch, "provider_id", None)
        status = public_batch_status(batch)
        if (type(provider) is str and 1 <= len(provider) <= 128
                and status is not None):
            statuses = self._online_source_statuses
            if provider in statuses or len(statuses) < 64:
                statuses[provider] = status
        if batch.results:
            self._remote_search_results = merge_results(self._remote_search_results, list(batch.results))
            self._populate_search(self._remote_search_results, announce=False)
        elif status is not None:
            # An unavailable/empty provider contributes no rows, but its
            # closed verdict must be visible now, not only after every other
            # federated provider has completed. Render the current request's
            # results (possibly empty); do not display a stale prior query.
            self._populate_search(self._remote_search_results, announce=False)
        return False
    def deliver_known_c(page):
        # The SQL cursor ran on the Search worker BEFORE any supplier GET.
        # GTK receives only already-admitted, non-actionable C descriptors.
        # Empty/partial C is never a proof of a zero-result remote catalogue.
        if not current():
            return False
        from ..core.notice_c_public_search import PublicNoticeCPage
        if type(page) is not PublicNoticeCPage or not page.hits:
            return False
        from ..core.notice_c_known_projection import merge_public_notice_c_known_rows
        rows = merge_public_notice_c_known_rows(
            [], page, identity_id=identity_id,
        )
        # Keep independently attested placements/rights already in the known
        # Search, replacing only this request's old public C preview keys.
        known = getattr(self, "_search_local_objects", None)
        preserved = {
            key: row for key, row in (known or {}).items()
            if type(row) is dict and row.get("source") != "notice_c_public"
        } if type(known) is dict else {}
        for row in rows:
            preserved[row["key"]] = row
        self._search_local_objects = preserved
        self._populate_search(self._remote_search_results, announce=False)
        return False
    def on_batch(batch):
        GLib.idle_add(deliver_batch, batch)
    def done(result):
        clear_pending()
        if not current(): return
        summary, rows = result
        if summary.cancelled:
            return
        self._online_search_warnings = tuple(summary.warnings)
        # Final ranking and exact deduplication belong to this request only.
        self._remote_search_results = list(rows)
        self._populate_search(self._remote_search_results, announce=True)
    class AdmittedSearchControl:
        def cancelled(self):
            return not request_scope.current(request_core)

    def online_work():
        # The registered reader/source epoch was captured before enqueue.
        # A real revocation stops I/O; GTK A→B→A only drops publication.
        if not request_scope.current(request_core):
            from ..plugin_api import Cancelled
            raise Cancelled("Recherche en ligne périmée")
        # #6366: C-first online extension without a second search engine.
        # This function runs in TaskRunner's worker; no SQLite read, supplier
        # HTTP, parse or consolidation is executed on the GTK thread.
        # The on_batch ABI and existing parallel provider orchestration stay
        # unchanged; an empty C cursor is merely partial, never a final zero.
        selected = tuple(dict.fromkeys(
            provider for provider in state.providers if provider != "local"
        ))
        c_reader = getattr(request_core, "search_public_notice_c", None)
        if (
            callable(c_reader)
            and type(state.text) is str
            and 2 <= len(state.text.strip()) <= 160
            and state.field in ("all", "title", "author", "publisher", "subject", "series", "collection", "language", "isbn")
            and state.scope in ("all", "digital", "physical", "unknown")
            and (not state.providers or selected)
        ):
            try:
                page = c_reader(
                    state.text, field=state.field, scope=state.scope,
                    provider_ids=selected, group_by_document=True,
                )
            except (TypeError, ValueError, OSError):
                # The ordinary remote extension remains available when the
                # bounded C cursor cannot be read. Never print raw DB data.
                page = None
            if page is not None and request_scope.current(request_core):
                GLib.idle_add(deliver_known_c, page)
        if not request_scope.current(request_core):
            from ..plugin_api import Cancelled
            raise Cancelled("Recherche en ligne périmée")
        return progressive_online_search(
            request_core, state, on_batch, control=AdmittedSearchControl(),
            request_scope=request_scope,
        )

    self.runner.submit("Recherche en ligne…", online_work, done, failed,
                       control=self.search_global_button, priority=TaskPriority.USER_ACTION, kind="search-online")
