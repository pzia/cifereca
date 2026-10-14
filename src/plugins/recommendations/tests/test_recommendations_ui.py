from types import SimpleNamespace

from recommendations import (
    UxRecommendationsView,
    _AFFINITY_MIN_TOOLTIP,
    _DIVERSITY_TOOLTIP,
    _candidate_languages,
    _external_book_url,
    _language_label,
    _library_overview_signature,
    _reference_label,
    _reference_selection_key,
    _refresh_on_first_map,
)


def ranked(title, score=50, provider="a"):
    return SimpleNamespace(
        item={"title": title, "author": "Auteur", "source_provider": provider},
        score=score,
        reasons=("raison",),
        sources=(provider,),
    )


def test_reference_label_uses_title_and_first_author():
    assert _reference_label({"title": "Le livre", "author": "Alice"}) == "Le livre — Alice"
    assert _reference_label({"title": "Sans auteur"}) == "Sans auteur"
    assert _reference_label({"author": "Alice"}) == "Sans titre — Alice"


def test_reference_selection_key_does_not_collapse_books_from_same_provider():
    first = SimpleNamespace(
        title="Livre A", authors=("Alice",), identifiers={"provider": "kobo"}
    )
    second = SimpleNamespace(
        title="Livre B", authors=("Alice",), identifiers={"provider": "kobo"}
    )
    assert _reference_selection_key(first) != _reference_selection_key(second)


def test_external_book_url_accepts_only_safe_http_sources():
    assert _external_book_url("https://example.org/book/1") == "https://example.org/book/1"
    assert _external_book_url("http://example.org/book/1") == "http://example.org/book/1"
    assert _external_book_url("javascript:alert(1)") is None
    assert _external_book_url("https://user:secret@example.org/book") is None
    assert _external_book_url("https:///missing-host") is None
    assert _external_book_url("https://example.org:bad/book") is None
    assert _external_book_url("https://example.org/book 1") is None
    assert _external_book_url("https://example.org/book/1?token=PRIVATE_SIG") is None
    assert _external_book_url("https://example.org/book/1#PRIVATE_SESSION") is None
    assert _external_book_url("https://127.0.0.1/book") is None
    assert _external_book_url("https://192.168.1.10/book") is None
    assert _external_book_url("http://localhost/book") is None
    assert _external_book_url("https://reader.local/book") is None
    assert _external_book_url("https://intranet/book") is None
    assert _external_book_url("https://example.org/a%0ab") is None
    assert _external_book_url("https://example.org/a%7fb") is None
    assert _external_book_url("https://example.org:443/book/1") == "https://example.org:443/book/1"


def test_affinity_tooltips_explain_low_high_effect_without_external_docs():
    assert "valeur faible" in _AFFINITY_MIN_TOOLTIP
    assert "valeur forte" in _AFFINITY_MIN_TOOLTIP
    assert "plus de résultats" in _AFFINITY_MIN_TOOLTIP
    assert "0 privilégie" in _DIVERSITY_TOOLTIP
    assert "valeur élevée" in _DIVERSITY_TOOLTIP
    assert "varie davantage" in _DIVERSITY_TOOLTIP


def test_partial_order_keeps_existing_rows_stable():
    view = object.__new__(UxRecommendationsView)
    view._provisional_order = {}
    first = view._stable_partial([ranked("A"), ranked("B")])
    assert [row.item["title"] for row in first] == ["A", "B"]

    # A later batch may score B before A; provisional UX must not jump.
    second = view._stable_partial([ranked("B", 90), ranked("A", 40), ranked("C", 80)])
    assert [row.item["title"] for row in second] == ["A", "B", "C"]


def test_candidate_languages_use_only_explicit_metadata_and_normalize_tags():
    item = {
        "title": "Multilingue",
        "metadata": {"language": "fre", "languages": ["en", "fr", "invalid language"]},
    }
    assert _candidate_languages(item) == ("fr", "en")
    assert _candidate_languages({"title": "Sans langue"}) == ()


def test_language_labels_are_human_readable_when_known():
    assert _language_label("fr") == "Français"
    assert _language_label("en") == "Anglais"
    assert _language_label("sv") == "sv"


def test_provider_ids_are_never_user_facing_when_label_is_known():
    view = object.__new__(UxRecommendationsView)
    view._provider_labels = {"": "Tous les catalogues", "a": "Catalogue A"}
    assert view._display_source("a") == "Catalogue A"
    assert view._display_source("unknown") == "unknown"


class _FakeWidget:
    def __init__(self):
        self.callback = None
        self.mapped = False

    def connect(self, signal, callback):
        assert signal == "map"
        self.callback = callback
        return 1

    def get_mapped(self):
        return self.mapped

    def emit_map(self):
        assert self.callback is not None
        self.mapped = True
        self.callback(self)


class _FakeGLib:
    def __init__(self):
        self.calls = []

    def idle_add(self, callback):
        self.calls.append(callback)
        return len(self.calls)


class _FakeView:
    def refresh(self):
        raise AssertionError("refresh must be scheduled, not executed synchronously")


def test_initial_refresh_waits_for_first_map_and_is_scheduled_once():
    widget = _FakeWidget()
    glib = _FakeGLib()
    view = _FakeView()

    _refresh_on_first_map(view, widget, glib)
    assert glib.calls == []

    widget.emit_map()
    assert glib.calls == [view.refresh]

    widget.emit_map()
    assert glib.calls == [view.refresh]


def test_already_mapped_widget_schedules_initial_refresh_immediately():
    widget = _FakeWidget()
    widget.mapped = True
    glib = _FakeGLib()
    view = _FakeView()

    _refresh_on_first_map(view, widget, glib)
    assert glib.calls == [view.refresh]


class _NoisyIdentityDropDown:
    def __init__(self):
        self.selected = 0
        self.callback = None
        self.notifications = 0
        self.sensitive = True

    def get_selected(self):
        return self.selected

    def set_selected(self, value):
        self.selected = int(value)
        self._notify()

    def set_sensitive(self, value):
        self.sensitive = bool(value)

    def _notify(self):
        self.notifications += 1
        if self.callback is not None:
            self.callback(self, None)


class _NoisyIdentityModel:
    def __init__(self, dropdown, labels=()):
        self.dropdown = dropdown
        self.items = list(labels)

    def get_n_items(self):
        return len(self.items)

    def remove(self, index):
        self.items.pop(index)
        self.dropdown._notify()

    def append(self, label):
        self.items.append(label)
        if len(self.items) == 1:
            self.dropdown.selected = 0
        self.dropdown._notify()


class _TrackingSnapshotCache:
    def __init__(self):
        self.invalidations = []

    def invalidate(self, identity_id=None):
        self.invalidations.append(identity_id)


class _CoalescingGLib:
    def __init__(self):
        self.next_id = 1
        self.sources = {}

    def timeout_add(self, _delay, callback):
        source_id = self.next_id
        self.next_id += 1
        self.sources[source_id] = callback
        return source_id

    def source_remove(self, source_id):
        self.sources.pop(source_id, None)

    def run_pending(self):
        callbacks = list(self.sources.items())
        self.sources.clear()
        for _source_id, callback in callbacks:
            callback()


def _identity_reload_view(rows, *, active_identity, busy=False):
    view = object.__new__(UxRecommendationsView)
    dropdown = _NoisyIdentityDropDown()
    model = _NoisyIdentityModel(dropdown, ("Default",))
    view._identity = dropdown
    view._identity_model = model
    view._identity_ids = ["default"]
    view._reloading_identities = False
    view._identity_library_signatures = {
        str(row.get("identity_id") or ""): _library_overview_signature(row)
        for row in rows
        if str(row.get("identity_id") or "")
    }
    view._library_snapshot_cache = _TrackingSnapshotCache()
    view._active_identity_id = active_identity
    view._busy = busy
    view._overview_refresh_source_id = 0
    view._overview_refresh_pending = False
    view.GLib = _CoalescingGLib()
    view.host = SimpleNamespace(identity_overview=lambda: list(rows))
    view.plugin = SimpleNamespace(context=None, _pending_reference=None)
    view._reference_book = None
    view._reference_books = []
    view._reload_reference_books = lambda books: None
    refreshes = []

    def refreshed():
        view._clear_overview_refresh()
        refreshes.append(view.selected_identity_id())

    view.refresh = refreshed
    dropdown.callback = view._identity_changed
    return view, dropdown, refreshes


def test_identity_reload_suppresses_transient_selection_refreshes():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True},
        {"identity_id": "other", "label": "Other", "selected": False},
    ]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")

    view._reload_identities()

    assert dropdown.notifications >= 3
    assert refreshes == []
    assert view._identity_ids == ["default", "other"]


def test_overview_burst_is_coalesced_to_one_recommendation_refresh():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True},
        {"identity_id": "other", "label": "Other", "selected": False},
    ]
    view, _dropdown, refreshes = _identity_reload_view(rows, active_identity="default")

    view._identity_overview_changed()
    view._identity_overview_changed()
    view._identity_overview_changed()

    assert refreshes == []
    assert len(view.GLib.sources) == 1

    view.GLib.run_pending()

    assert refreshes == ["default"]


def test_overview_burst_while_busy_waits_for_current_calculation():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True},
        {"identity_id": "other", "label": "Other", "selected": False},
    ]
    view, _dropdown, refreshes = _identity_reload_view(
        rows,
        active_identity="default",
        busy=True,
    )

    view._identity_overview_changed()
    view._identity_overview_changed()

    assert view._overview_refresh_pending is True
    assert view.GLib.sources == {}
    assert refreshes == []

    view._busy = False
    view._schedule_overview_refresh()
    view.GLib.run_pending()

    assert refreshes == ["default"]


def test_identity_overview_before_first_map_does_not_duplicate_initial_refresh():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True},
        {"identity_id": "other", "label": "Other", "selected": False},
    ]
    view, _dropdown, refreshes = _identity_reload_view(rows, active_identity=None)

    view._identity_overview_changed()

    assert view.GLib.sources == {}
    assert refreshes == []


def test_identity_reload_prefers_core_selected_identity_regardless_of_row_order():
    rows = [
        {"identity_id": "other", "label": "Other", "selected": True},
        {"identity_id": "default", "label": "Default", "selected": False},
    ]
    view, _dropdown, refreshes = _identity_reload_view(rows, active_identity="default")

    view._reload_identities()

    assert view.selected_identity_id() == "other"
    assert refreshes == []


def test_overview_selection_change_clears_reference_from_previous_identity():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True, "statistics": {"local_books": 562}, "sync": {"sync_run_id": "run-1", "status": "completed"}},
        {"identity_id": "other", "label": "Other", "selected": False, "statistics": {"local_books": 15}, "sync": {"sync_run_id": "run-2", "status": "completed"}},
    ]
    view, _dropdown, _refreshes = _identity_reload_view(rows, active_identity="default")
    reference = object()
    view._reference_book = reference
    view._reference_books = [reference]
    view.plugin._pending_reference = reference

    rows[0]["selected"] = False
    rows[1]["selected"] = True
    view._identity_overview_changed()

    assert view.selected_identity_id() == "other"
    assert view._reference_book is None
    assert view._reference_books == []
    assert view.plugin._pending_reference is None


def test_overview_selection_change_keeps_library_snapshot_cached():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True, "statistics": {"local_books": 562}, "sync": {"sync_run_id": "run-1", "status": "completed"}},
        {"identity_id": "other", "label": "Other", "selected": False, "statistics": {"local_books": 15}, "sync": {"sync_run_id": "run-2", "status": "completed"}},
    ]
    view, _dropdown, _refreshes = _identity_reload_view(rows, active_identity="default")

    rows[0]["selected"] = False
    rows[1]["selected"] = True
    view._identity_overview_changed()

    assert view._library_snapshot_cache.invalidations == []


def test_overview_library_change_invalidates_only_affected_identity():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True, "statistics": {"local_books": 562}, "sync": {"sync_run_id": "run-1", "status": "completed"}},
        {"identity_id": "other", "label": "Other", "selected": False, "statistics": {"local_books": 15}, "sync": {"sync_run_id": "run-2", "status": "completed"}},
    ]
    view, _dropdown, _refreshes = _identity_reload_view(rows, active_identity="default")

    rows[1]["statistics"]["local_books"] = 16
    view._identity_overview_changed()

    assert view._library_snapshot_cache.invalidations == ["other"]


def test_overview_sync_change_invalidates_active_identity():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True, "statistics": {"local_books": 562}, "sync": {"sync_run_id": "run-1", "status": "running"}},
    ]
    view, _dropdown, _refreshes = _identity_reload_view(rows, active_identity="default")

    rows[0]["sync"]["status"] = "completed"
    rows[0]["sync"]["finished_at"] = "2026-10-08T09:30:00Z"
    view._identity_overview_changed()

    assert view._library_snapshot_cache.invalidations == ["default"]


def test_legacy_overview_without_freshness_projection_stays_conservative():
    rows = [{"identity_id": "default", "label": "Default", "selected": True}]
    view, _dropdown, _refreshes = _identity_reload_view(rows, active_identity="default")

    view._identity_overview_changed()

    assert view._library_snapshot_cache.invalidations == ["default"]


class _FakeDropDown:
    def __init__(self, selected=0):
        self.selected = selected

    def get_selected(self):
        return self.selected

    def set_selected(self, value):
        self.selected = int(value)


class _FakeStringList:
    def __init__(self, values):
        self.values = list(values)

    def get_n_items(self):
        return len(self.values)

    def remove(self, index):
        self.values.pop(index)

    def append(self, value):
        self.values.append(value)


def _language_filter_view(selected_language=""):
    view = object.__new__(UxRecommendationsView)
    view._language_ids = [""] + ([selected_language] if selected_language else [])
    view._language = _FakeDropDown(1 if selected_language else 0)
    view._language_model = _FakeStringList(["Toutes"] + ([_language_label(selected_language)] if selected_language else []))
    view._reloading_languages = False
    return view


def test_language_reload_preserves_selected_language_even_when_temporarily_absent():
    view = _language_filter_view("fr")
    rows = [
        SimpleNamespace(item={"metadata": {"language": "en"}}),
        SimpleNamespace(item={"metadata": {"languages": ["de", "en"]}}),
    ]

    view._reload_languages(rows)

    assert view.selected_language_id() == "fr"
    assert set(view._language_ids) == {"", "de", "en", "fr"}


def test_precise_language_filter_excludes_unknown_and_accepts_multilingual_candidate():
    view = _language_filter_view("fr")
    view._score = SimpleNamespace(get_value=lambda: 0)
    view._provider_ids = [""]
    view._provider = _FakeDropDown(0)
    view._text = SimpleNamespace(get_text=lambda: "")

    french = ranked("FR")
    french.item["metadata"] = {"language": "fr"}
    multilingual = ranked("FR+EN")
    multilingual.item["metadata"] = {"languages": ["en", "fr"]}
    unknown = ranked("Unknown")

    assert view._matches(french)
    assert view._matches(multilingual)
    assert not view._matches(unknown)


def test_all_languages_mode_keeps_unknown_language_candidate():
    view = _language_filter_view("")
    view._score = SimpleNamespace(get_value=lambda: 0)
    view._provider_ids = [""]
    view._provider = _FakeDropDown(0)
    view._text = SimpleNamespace(get_text=lambda: "")

    assert view._matches(ranked("Unknown"))
