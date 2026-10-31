"""Search result view adapter; MainWindow retains task and identity ownership.

The host supplies the existing controls, selection, cached objects and service.
This module renders already-loaded data only: it never starts a provider search,
changes a generation, or introduces an additional subscription.
"""
from __future__ import annotations

from typing import Any
from ..models import SearchResult
from ..core.search import file_format, local_match_explanation
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
    # Only Search batches for the current request may later be opened as
    # *exact* notices from a plugin. Cached rows from an old identity/query
    # never acquire a fresh stamp by being re-rendered.
    identity = getattr(getattr(self.service, "current_identity", None),
                       "identity_id", None)
    state = self.search_controls.state()
    context = (state.text, state.field, state.scope, identity)
    self._loaded_search_context = (
        context if identity and
        getattr(self, "_online_source_context", None) == context
        else None
    )
    self.service.last_search = rows
    self.search_objects = dict(zip(_remote_result_row_keys(rows), rows))
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



def _exact_known_catalogue_scope(row: dict[str, Any]) -> tuple[str, ...] | None:
    """Trust the already-scoped SQLite v2 coordinates, not title or ISBN."""
    meta = row.get("metadata")
    if type(meta) is not dict or meta.get("catalogue_scope_version") != 2:
        return None
    return _catalogue_edition_scope(
        row.get("provider_id"), meta.get("source_credential"),
        row.get("item_id"), meta.get("docbase"), meta.get("source_collection"),
    )


def _catalogue_edition_scope(
    provider: object, credential: object, record: object,
    docbase: object, collection: object,
) -> tuple[str, ...] | None:
    if (any(type(value) is not str or not value or len(value) > 512
            for value in (provider, credential, record, docbase))
            or (collection is not None and
                (type(collection) is not str or len(collection) > 512))):
        return None
    return (provider, credential, record, docbase, collection or "")


def _exact_remote_catalogue_scope(item: SearchResult) -> tuple[str, ...] | None:
    return _catalogue_edition_scope(
        item.source_provider, item.source_credential, item.record_id,
        item.docbase, item.source_collection,
    )


def _remote_result_row_keys(rows: list[SearchResult]) -> list[str]:
    """Give colliding supplier display IDs distinct, stable UI-only keys.

    The provider's legacy `id` may omit credential/collection. A duplicate
    key must not overwrite one exact edition in search_objects or selection.
    This hashed key neither verifies rights nor changes upstream record IDs.
    """
    from collections import Counter
    from hashlib import sha256

    bases = [f"{item.source_provider}:{item.id}" for item in rows]
    counts = Counter(bases)
    keys: list[str] = []
    used: set[str] = set()
    for index, (base, item) in enumerate(zip(bases, rows)):
        if counts[base] == 1 and base not in used:
            key = base
        else:
            scope = _exact_remote_catalogue_scope(item)
            if scope is not None:
                key = f"{base}:scope:{sha256(repr(scope).encode('utf-8')).hexdigest()}"
            else:
                # Historical/unqualified notices remain visible but are not
                # silently assigned another edition's verified coordinates.
                key = f"{base}:unqualified:{index}"
            if key in used:
                key = f"{base}:duplicate:{index}"
        keys.append(key)
        used.add(key)
    return keys


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
                " · ".join(x for x in [self._library_state_label(item), local_match_explanation(item, state.text, field=state.field)] if x),
                self.service.provider_display_name(provider) if provider != "local" else "Local",
                item.get("thumbnail_url"),
            ))
        self._attach_row_facets(local_rows, self._search_local_objects, self.search_controls)
        self.search_page.show_rows(local_rows)
        self._search_local_snapshot_dirty = False
        shown = len(local_rows)
        self._update_search_count(shown)
        return shown
    from cifereca.gui.offer_actions import requires_selected_offer_check, download_action_label

    identity = self.service.current_identity
    linked = self.service.local_placements_for_sources(identity.identity_id) if identity else {}
    # A public Kobo catalog 'owned' flag is not an authenticated entitlement.
    # Read the Core's identity-scoped rights once per render, not per book.
    kobo_rights = (
        self.service.kobo_personal_right_states(identity.identity_id)
        if identity and any(item.source_provider == "kobo" for item in self._remote_search_results)
        else {}
    )
    remote_keys = {
        (item.source_provider, item.source_credential, item.record_id)
        for item in self._remote_search_results
    }
    remote_exact = {
        scope for item in self._remote_search_results
        if (scope := _exact_remote_catalogue_scope(item)) is not None
    }
    # A legacy acquisition link is only (provider, credential, product).
    # If multiple editions share those coordinates, never assign B's local
    # placement/open action to A solely because their product IDs match.
    variants: dict[tuple[str, str, str], set[tuple[str, ...]]] = {}
    for scope in remote_exact:
        variants.setdefault(scope[:3], set()).add(scope)
    for known in self._search_local_objects.values():
        scope = _exact_known_catalogue_scope(known)
        if scope is not None:
            variants.setdefault(scope[:3], set()).add(scope)
    ambiguous = {key for key, scopes in variants.items() if len(scopes) > 1}
    linked_placements = {
        linked[key]["placement_id"] for key in remote_keys
        if key in linked and key not in ambiguous
    }
    ui_rows: list[BookListItem] = []
    for known in state.filter(self._search_local_objects.values()):
        if known.get("placement_id") in linked_placements:
            continue
        # Suppress only the exact same v2 account + docbase + collection row.
        # Historical unscoped rows may look redundant but are not proof that
        # they correspond to the current live supplier edition.
        exact_known = _exact_known_catalogue_scope(known)
        if not known.get("local") and exact_known in remote_exact:
            continue
        ui_rows.append(BookListItem(str(known["key"]), str(known.get("title") or "Sans titre"), str(known.get("author") or ""),
            local_match_explanation(known, state.text, field=state.field),
            self.service.provider_display_name(str(known.get("provider_id") or "local")), known.get("thumbnail_url")))
    remote_rows = list(zip(
        _remote_result_row_keys(self._remote_search_results),
        self._remote_search_results,
    ))
    filtered = {
        row["key"] for row in state.filter([
            {**result_row(item), "key": key} for key, item in remote_rows
        ])
    }
    for key, item in remote_rows:
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
        if item.source_provider == "numilog-catalog":
            # An anonymous commercial notice is not an owned ebook, even if
            # the provider unexpectedly supplies owned/downloadable=True.
            extras.append("Catalogue public Numilog · aucun achat attesté")
        elif item.source_provider == "numilog" and result_row(item)["state"] == "remote_owned":
            # Private inventory, NOT a locally available EPUB/ACSM.
            extras.append("Mes achats Numilog · fichier local non présumé")
        # Core's exact identity/credential/product entitlement is the only
        # purchase authority, not the provider's raw catalogue owned flag.
        right_state = (
            kobo_rights.get((item.source_credential, item.record_id))
            if item.source_provider == "kobo" and not stale_known else None
        )
        kobo_download_ready = False
        if item.source_provider == "kobo" and not stale_known:
            if right_state == "active":
                extras.append("Possédé sur Kobo · Mes achats (droit attesté)")
                kobo_download_ready = self.service.can_direct_download(item)
                if not kobo_download_ready:
                    extras.append("Sauvegarde Kobo indisponible")
            elif right_state == "archived":
                extras.append("Archivé sur Kobo · droit non actif")
            else:
                extras.append("Catalogue Kobo · aucun achat attesté pour ce profil")
                if item.price:
                    extras.append(f"{item.price} {item.currency or ''}".strip())
        meta = " · ".join(x for x in [medium, *extras, item.published_at] if x)
        # An offline public result is never evidence of a current entitlement,
        # a download offer or a verified local placement.
        placement_key = (item.source_provider, item.source_credential, item.record_id)
        placement = None if (stale_known or placement_key in ambiguous) else linked.get(
            placement_key
        )
        # Public Numilog has neither an authenticated download entitlement
        # nor an acquisition action. Never consult the direct-download broker
        # for this source, including malformed remote flags.
        direct = (
            kobo_download_ready if item.source_provider == "kobo"
            else False if (stale_known or item.source_provider == "numilog-catalog"
                           or (item.source_provider == "numilog"
                               and result_row(item)["state"] != "remote_owned"))
            else self.service.can_direct_download(item)
        )
        # An installed v2 interface is a preflight hint, not an attested offer.
        check_required = bool(direct and not placement and
                              requires_selected_offer_check(self.service, item))
        if check_required:
            extras.append("Offre à vérifier avant acquisition")
            meta = " · ".join(x for x in [medium, *extras, item.published_at] if x)
        callback = None
        if placement:
            extras.append('Fichier local disponible')
            meta = ' · '.join(x for x in [medium, *extras, item.published_at] if x)
            callback = lambda _button, selected=placement: self._open_placement(selected)
        elif direct:
            callback = lambda button, selected=item: self.download_direct_item(selected, button)
        ui_rows.append(BookListItem(
            key, item.title, item.author or "", meta, source, item.thumbnail_url,
            action_label="Ouvrir" if placement else (download_action_label(item, check_required=check_required) if direct else None),
            action_callback=callback,
            action_icon_name="document-open-symbolic" if placement else ("document-save-symbolic" if direct else None),
        ))
    objects = {
        **self._search_local_objects,
        **{key: result_row(item) for key, item in remote_rows},
    }
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

