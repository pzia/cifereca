"""Search result view adapter; MainWindow retains task and identity ownership.

The host supplies the existing controls, selection, cached objects and service.
This module renders already-loaded data only: it never starts a provider search,
changes a generation, or introduces an additional subscription.
"""
from __future__ import annotations

from typing import Any
from ..models import SearchResult
from ..core.search import file_format
from .book_widgets import BookListItem
from .query_model import result_row


def _stale_catalog_provenance(metadata: Any) -> tuple[str, ...]:
    """Explain a cached *public notice* without implying live availability.

    `mirrored_at` is our local index timestamp, not a publication date,
    download entitlement, or a last-seen-upstream claim.
    """
    if not isinstance(metadata, dict) or metadata.get("search_provenance") != "known_catalog_stale":
        return ()
    from datetime import datetime, timezone

    labels = ["Résultat connu · hors ligne"]
    observed = metadata.get("mirrored_at")
    if type(observed) is int and observed > 0:
        try:
            date = datetime.fromtimestamp(observed, tz=timezone.utc).strftime("%d/%m/%Y")
        except (OverflowError, ValueError, OSError):
            pass
        else:
            labels.append(f"Notice indexée localement le {date} (UTC)")
    return tuple(labels)


def _sync_search_detail_after_results(self) -> None:
    if not hasattr(self, "search_page"):
        return
    selected = self.search_page.selection.get_selected_item()
    if isinstance(selected, BookListItem):
        return
    self._search_selected(None)


def _populate_local_search(self, rows: list[dict[str, Any]]) -> None:
    self._search_local_objects = {str(row.get("key") or f"local:{index}"): row for index, row in enumerate(rows)}
    self._search_mode = "local"
    self._loaded_search_context = None
    self.search_objects = {}
    shown = self._render_search_results()
    self._sync_search_detail_after_results()
    self.search_global_button.set_sensitive(True)


def _populate_search(self, rows: list[SearchResult], *, announce: bool = True) -> None:
    self._search_mode = "global"
    self.service.last_search = rows
    self.search_objects = {f"{x.source_provider}:{x.id}": x for x in rows}
    shown = self._render_search_results()
    self._sync_search_detail_after_results()
    if not announce:
        return
    # Providers may include arbitrary exception strings (signed URLs, tokens,
    # account IDs or local paths) in search warnings. Never echo their text
    # into GTK. The presence of a partial failure is sufficient for UX.
    if getattr(self.service, "last_search_warnings", None):
        # Read-only fallback rows are not a fresh remote response. Make the
        # degraded provenance explicit without ever echoing provider errors.
        if any(
            (getattr(item, "metadata", None) or {}).get("search_provenance")
            == "known_catalog_stale" for item in rows
        ):
            self.toast("Résultats connus, recherche en ligne indisponible · résultats partiels.", 6)
        else:
            self.toast("Résultats partiels · certaines sources sont indisponibles.", 6)


def _render_search_results(self) -> int:
    if not hasattr(self, "search_page"):
        return 0
    state = self.search_controls.state()
    if self._search_mode == "local":
        audio_formats = {"mp3", "m4a", "ogg", "opus", "flac", "wav"}
        local_rows: list[BookListItem] = []
        for item in state.filter(self._search_local_objects.values()):
            key = str(item["key"])
            media_format = str(item.get("media_format") or "").casefold()
            content_type = "audiobook" if media_format in audio_formats else "ebook"
            provider = str(item.get("provider_id") or "local")
            local_rows.append(BookListItem(
                key, str(item.get("title") or "Sans titre"), str(item.get("author") or ""),
                " · ".join(x for x in [self._library_state_label(item)] if x),
                self.service.provider_display_name(provider) if provider != "local" else "Local",
                item.get("thumbnail_url"),
            ))
        self._attach_row_facets(local_rows, self._search_local_objects, self.search_controls)
        self.search_page.show_rows(local_rows)
        self._search_local_snapshot_dirty = False
        shown = len(local_rows)
        self._update_search_count(shown)
        return shown
    identity = self.service.current_identity
    linked = self.service.local_placements_for_sources(identity.identity_id) if identity else {}
    # A public Kobo catalog 'owned' flag is not an authenticated entitlement.
    # Read the Core's identity-scoped rights once per render, not per book.
    kobo_rights = (
        self.service.kobo_personal_right_states(identity.identity_id)
        if identity and any(item.source_provider == "kobo" for item in self._remote_search_results)
        else {}
    )
    remote_keys = {(item.source_provider, item.source_credential, item.record_id) for item in self._remote_search_results}
    linked_placements = {linked[key]['placement_id'] for key in remote_keys if key in linked}
    ui_rows: list[BookListItem] = []
    for known in state.filter(self._search_local_objects.values()):
        if known.get('placement_id') in linked_placements: continue
        known_key = (known.get('provider_id'), known.get('source_credential') or (known.get('metadata') or {}).get('source_credential'), str(known.get('item_id') or ''))
        if known_key in remote_keys and not known.get('local'): continue
        ui_rows.append(BookListItem(str(known["key"]), str(known.get("title") or "Sans titre"), str(known.get("author") or ""),
            "", self.service.provider_display_name(str(known.get("provider_id") or "local")), known.get("thumbnail_url")))
    filtered = {row["key"] for row in state.filter([{**result_row(x), "key": f"{x.source_provider}:{x.id}"} for x in self._remote_search_results])}
    for item in self._remote_search_results:
        key = f"{item.source_provider}:{item.id}"
        if key not in filtered: continue
        content_type = self.service.content_type_for(item)
        if item.medium == "physical":
            medium = "Physique"
        elif item.medium == "unknown" and not file_format(item.format):
            medium = "Support indéterminé"
        elif content_type == "audiobook":
            medium = "Livre audio"
        else:
            medium = "Livre numérique"
        source = self.service.provider_display_name(item.source_provider)
        stale_known = (item.metadata or {}).get("search_provenance") == "known_catalog_stale"
        extras: list[str] = list(_stale_catalog_provenance(item.metadata)) if stale_known else []
        if item.source_provider == "kobo" and not stale_known:
            right_state = kobo_rights.get((item.source_credential, item.record_id))
            if right_state == "active":
                extras.append("Possédé sur Kobo")
                if not self.service.can_direct_download(item):
                    extras.append("Kobo-Adept indisponible")
            elif right_state == "archived":
                extras.append("Archivé sur Kobo")
            elif item.price:
                extras.append(f"{item.price} {item.currency or ''}".strip())
        meta = " · ".join(x for x in [medium, *extras, item.published_at] if x)
        # An offline public result is never evidence of a current entitlement,
        # a download offer or a verified local placement.
        placement = None if stale_known else linked.get(
            (item.source_provider, item.source_credential, item.record_id)
        )
        direct = False if stale_known else self.service.can_direct_download(item)
        callback = None
        if placement:
            extras.append('Fichier local disponible')
            meta = ' · '.join(x for x in [medium, *extras, item.published_at] if x)
            callback = lambda _button, selected=placement: self._open_placement(selected)
        elif direct:
            callback = lambda button, selected=item: self.download_direct_item(selected, button)
        ui_rows.append(BookListItem(
            key, item.title, item.author or "", meta, source, item.thumbnail_url,
            action_label="Ouvrir" if placement else (("Sauvegarder" if item.source_provider == "kobo" else "Télécharger") if direct else None),
            action_callback=callback,
            action_icon_name="document-open-symbolic" if placement else ("document-save-symbolic" if direct else None),
        ))
    objects = {**self._search_local_objects, **{f"{item.source_provider}:{item.id}": result_row(item) for item in self._remote_search_results}}
    self._attach_row_facets(ui_rows, objects, self.search_controls)
    self.search_page.show_rows(ui_rows)
    self._search_local_snapshot_dirty = False
    shown = len(ui_rows)
    self._update_search_count(shown)
    return shown


def _update_search_count(self, shown: int) -> None:
    if not hasattr(self, "search_page"):
        return
    count = max(0, int(shown))
    noun = "résultat" if count == 1 else "résultats"
    scope = "Connu" if self._search_mode == "local" else "Étendu"
    suffix, tooltip = "", ""
    if self._search_mode == "global" and getattr(self, "search_controls", None) is not None:
        state = self.search_controls.state()
        identity = getattr(self.service, "current_identity", None)
        context = (state.text, state.field, state.scope,
                   getattr(identity, "identity_id", None))
        if getattr(self, "_online_source_context", None) == context:
            from cifereca.gui.search_batch_feedback import safe_source_statuses
            suffix, tooltip = safe_source_statuses(
                getattr(self, "_online_source_statuses", None)
            )
    self.search_page.count_label.set_label(
        f"{count} {noun} · {scope}{suffix}"
    )
    # Tooltip contains closed public status counts only. Older headless
    # adapters without GTK tooltip support retain the unchanged count.
    label = self.search_page.count_label
    if hasattr(label, "set_tooltip_text"):
        label.set_tooltip_text(tooltip or None)

