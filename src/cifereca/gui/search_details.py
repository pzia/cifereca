"""Search-detail view adapter; the host retains identity and task generations.

Every asynchronous result uses the existing captured context. Selection,
cache cancellation, task runner, UI-thread delivery and service ownership stay
on MainWindow. No import of MainWindow or new subscription belongs here.
"""
from __future__ import annotations

from .search_host_port import SearchDetailHost

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..models import SearchResult, ItemDetail
from .book_widgets import BookListItem
from .query_model import result_row
from .detail_model import book_detail_presentation, detail_language
from .local_book_detail_sections import local_book_detail_sections
from .task_runner import TaskPriority
from .search_detail_context import detail_callback_is_current


def _search_selected(self: SearchDetailHost, row: BookListItem | None) -> None:
    if getattr(self, "_closed", False):
        return
    # A result id alone is not enough: ids can be reused by another
    # provider, selection, query, or identity while holdings are in flight.
    self._search_detail_request_generation += 1
    self._cancel_cached_book_details()
    if row is None:
        self._selected_search_id = None
        self.search_page.detail.empty("Aucun document sélectionné", "Sélectionnez un résultat pour afficher ses détails.")
        return
    if row.key in self._search_local_objects:
        item = self._search_local_objects.get(row.key)
        if not item:
            return
        self._selected_search_id = row.key
        description = self._book_description(item)
        self.search_page.detail.show_presentation(book_detail_presentation(
            title=str(item.get("title") or "Sans titre"),
            subtitle=str(item.get("author") or ""),
            thumbnail=item.get("thumbnail_url"),
            badges=[],
            section_rows=local_book_detail_sections(
                item,
                provider_display_name=self.service.provider_display_name,
                availability_label=self._library_availability_label,
                identity_by_id=self.service.identity_by_id,
                sync_status_label=self._kobo_backup_status_label,
                language_label=detail_language,
                offline_if_local=True,
            ),
            metadata=item.get('metadata'),
        ), description=description)
        actions = []
        open_library = Gtk.Button(label="Voir dans Bibliothèque")
        open_library.connect("clicked", lambda _b, key=row.key: self._open_library_item(key))
        if item.get("local") or item.get("state") in {"remote_owned", "remote_loan_active", "remote_loan_realized"}:
            actions.append(open_library)
        if item.get("source") == "known_catalog":
            candidate = self._known_search_result(item)
            if candidate:
                # A cached button may outlive a selection, new search or
                # identity A→B→A. Check the captured Search generation BEFORE
                # requesting holdings, not only when the response returns.
                context = self._search_detail_request_context()
                expected_key = row.key
                show = Gtk.Button(label="Disponibilités / téléchargement")
                show.connect(
                    "clicked",
                    lambda _b, notice=candidate, key=expected_key, ctx=context: (
                        self._load_known_detail(notice)
                        if self._search_detail_context_current(key, ctx) else None
                    ),
                )
                actions.append(show)
        extend = Gtk.Button(label="En ligne")
        extend.add_css_class("suggested-action")
        extend.connect("clicked", lambda _b: self.run_search(True)); actions.append(extend)
        self.search_page.detail.add_actions(actions)
        self._add_book_notice(self.search_page.detail,item, include_description=False)
        self._add_local_book_actions(self.search_page.detail,item)
        self._add_facets(self.search_page.detail, item, self.search_controls)
        # Local dictionary presentations do not run DetailPanel._decorate.
        # Remote SearchResult presentations already own that decoration.
        self._add_plugin_book_actions(self.search_page.detail, item)
        self._request_cached_book_details("search", row.key, item)
        return
    item = self.search_objects.get(row.key)
    if not item: return
    self._selected_search_id = item.id
    if (getattr(item, "metadata", None) or {}).get("search_provenance") == "known_catalog_stale":
        # The stale catalogue is read-only evidence. Merely selecting it must
        # not start a holdings/authentication request to a provider in outage.
        self.search_page.detail.show_search(item, loading=False)
        self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
        return
    self.search_page.detail.show_search(item, loading=True)
    self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
    self._add_local_book_actions(self.search_page.detail,result_row(item))
    self._request_cached_book_details("search", item.id, result_row(item))
    selected_id = item.id
    context = self._search_detail_request_context()
    self.runner.submit(
        "Chargement des disponibilités…",
        lambda: self.service.holdings(item, self._last_search_query, force=False),
        lambda detail: self._apply_search_detail(selected_id, item, detail, context=context),
        lambda exc: self._search_detail_error(selected_id, item, exc, context=context),
        priority=TaskPriority.METADATA, kind="holdings",
    )


def _search_detail_request_context(self: SearchDetailHost) -> tuple[int, int, int, str | None]:
    identity = self.service.current_identity
    return (
        self._search_detail_request_generation,
        self._identity_context_generation,
        getattr(self, "_search_generation", 0),
        identity.identity_id if identity is not None else None,
    )


def _search_detail_context_current(
    self: SearchDetailHost, selected_id: str, context: tuple[int, int, int, str | None] | None,
) -> bool:
    if self._closed or self._selected_search_id != selected_id:
        return False
    return detail_callback_is_current(
        closed=False, selected_id=selected_id, expected_id=selected_id,
        captured=context,
        current=self._search_detail_request_context() if context is not None else None,
    )


def _apply_search_detail(
    self: SearchDetailHost, selected_id: str, item: SearchResult, detail: ItemDetail,
    *, context: tuple[int, int, int, str | None] | None = None,
) -> None:
    if not self._search_detail_context_current(selected_id, context):
        return
    self.search_page.detail.show_item_detail(item, detail)
    self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
    self._add_local_book_actions(self.search_page.detail, result_row(item))
    self._request_cached_book_details("search", item.id, result_row(item))
    # Keep unrelated provider callbacks (and AST-isolated host tests)
    # independent of the optional Numilog UI adapter.
    if getattr(item, "source_provider", None) in {"numilog", "numilog-catalog"}:
        from .numilog_enrichment_action import add_numilog_enrichment_action
        add_numilog_enrichment_action(
            self, selected_id, item, detail, context,
            button_factory=self.search_page.detail.window.action_button,
        )


def _search_detail_error(
    self: SearchDetailHost, selected_id: str, item: SearchResult, exc: BaseException,
    *, context: tuple[int, int, int, str | None] | None = None,
) -> None:
    if not self._search_detail_context_current(selected_id, context):
        return
    self.search_page.detail.show_search(item, loading=False)
    self._add_local_book_actions(self.search_page.detail, result_row(item))
    self._request_cached_book_details("search", item.id, result_row(item))
    # Provider exceptions can embed account IDs, private file paths or signed
    # URLs. Do not echo their arbitrary text into a user-visible GTK toast.
    self.toast("Disponibilités indisponibles. Réessayez ultérieurement.", 5)


def _load_known_detail(self: SearchDetailHost, item):
    if getattr(self, "_closed", False):
        return
    # Explicitly opening a known-catalogue detail supersedes a previous
    # holdings request, even if the selected list key stays unchanged.
    self._search_detail_request_generation += 1
    selected = self._selected_search_id
    context = self._search_detail_request_context()
    self.runner.submit("Disponibilités…", lambda: self.service.holdings(item, self._last_search_query, force=False),
        lambda detail: self._apply_search_detail(selected, item, detail, context=context),
        priority=TaskPriority.USER_ACTION, kind="holdings")
