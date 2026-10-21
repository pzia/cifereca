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
    request_identity = self.service.current_identity.identity_id if self.service.current_identity else None
    online_key = (state, request_identity)
    if force and pending_online_search_matches(
        getattr(self, "_online_pending_key", None), online_key,
        getattr(self, "_online_pending_generation", None),
        self._search_generation,
    ):
        return
    self._search_generation += 1
    # A previous Search result must not remain navigable from a plugin while
    # the next request is pending, even if both use the same query text.
    self._loaded_search_context = None
    generation = self._search_generation
    identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
    # Never reuse source verdicts across requests, accounts or generations.
    self._online_source_statuses = {}
    self._online_source_context = (
        (state.text, state.field, state.scope, identity_id) if force else None
    )
    def current():
        if getattr(self, "_closed", False):
            return False
        identity = self.service.current_identity.identity_id if self.service.current_identity else None
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
        def done(rows):
            if current(): self._populate_local_search(rows)
        self.runner.submit("Recherche dans le connu…",
            lambda: self.service.search_known_catalog(state.text, state.scope, field=state.field, identity_id=identity_id),
            done, control=self.search_button, priority=TaskPriority.USER_ACTION, kind="search-known")
        return
    # Retain all already-loaded provider/type results for instant reversal
    # of facet changes. A different text/field begins a new result universe.
    request_key = (state.text, state.field, state.scope)
    if request_key != getattr(self, "_remote_query_key", None):
        self._remote_search_results = []
        if state.text != self._last_search_query: self._search_local_objects = {}
    self._remote_query_key = request_key
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
    def on_batch(batch):
        GLib.idle_add(deliver_batch, batch)
    def done(summary):
        clear_pending()
        if not current(): return
        self.service.last_search_warnings = list(summary.warnings)
        # Batch delivery is progressive; the final render only reconciles
        # ranking/deduplication with the authoritative aggregate.
        self._remote_search_results = merge_results(self._remote_search_results, list(self.service.last_search))
        self._populate_search(self._remote_search_results, announce=True)
    self.runner.submit("Recherche en ligne…", lambda: progressive_online_search(self.service, state, on_batch), done, failed,
                       control=self.search_global_button, priority=TaskPriority.USER_ACTION, kind="search-online")
