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
    self._search_detail_cover_by_key = {}
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
    self.search_objects = dict(zip(_remote_result_row_keys(rows), rows))
    shown = self._render_search_results()
    self._sync_search_detail_after_results()
    if not announce:
        return
    # Providers may include arbitrary exception strings (signed URLs, tokens,
    # account IDs or local paths) in search warnings. Never echo their text
    # into GTK. The presence of a partial failure is sufficient for UX.
    if getattr(self, "_online_search_warnings", ()):
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
    """Compare one known source/edition without a title/ISBN/image join."""
    meta = row.get("metadata")
    if type(meta) is not dict:
        return None
    version = meta.get("catalogue_scope_version")
    if version not in (2, 3):
        return None
    base = _catalogue_edition_scope(
        row.get("provider_id"), meta.get("source_credential"),
        row.get("item_id"), meta.get("docbase"), meta.get("source_collection"),
    )
    if base is None or version == 2:
        return base
    import re
    revision = meta.get("source_revision")
    account = meta.get("source_account_id")
    medium = meta.get("medium")
    if (type(revision) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", revision) is None
            or medium not in ("digital", "physical", "unknown")
            or (account is not None and (
                type(account) is not str or not account
                or len(account) > 512 or "\0" in account
            ))):
        return None
    return (*base, account or "", medium, revision)


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
    base = _catalogue_edition_scope(
        item.source_provider, item.source_credential, item.record_id,
        item.docbase, item.source_collection,
    )
    if base is None or item.revision_id in (None, ""):
        return base
    import re
    revision, account, medium = (
        item.revision_id, item.source_account_id, item.medium,
    )
    if (type(revision) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", revision) is None
            or medium not in ("digital", "physical", "unknown")
            or (account is not None and (
                type(account) is not str or not account
                or len(account) > 512 or "\0" in account
            ))):
        return None
    return (*base, account or "", medium, revision)


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
                # Separate accounts and revisions even when a legacy row ID
                # and catalogue coordinates collide. This display key is
                # never ownership evidence and never aliases another account.
                exact = (*scope, item.source_account_id or "", item.revision_id or "")
                key = f"{base}:scope:{sha256(repr(exact).encode('utf-8')).hexdigest()}"
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
            cover_url = item.get("thumbnail_url")
            # The selected *known catalogue v2* notice may have acquired a
            # safe holdings image after this local Search list was built.
            # Reuse N2 only after Core requalifies the exact same source under
            # the captured reading identity; no title/ISBN/legacy-row join.
            bridge = getattr(self, "_search_cover_bridge", None)
            if bridge is not None:
                from cifereca.gui.search_cover_bridge import SearchCoverBridge
                from cifereca.core.known_catalog_provenance import (
                    known_catalog_search_result,
                )
                identity_id = getattr(
                    getattr(self.service, "current_identity", None),
                    "identity_id", None,
                )
                if isinstance(bridge, SearchCoverBridge):
                    exact_notice = known_catalog_search_result(
                        item, identity_id=identity_id,
                    )
                    if exact_notice is not None:
                        resolved_uri = bridge.selected_url(
                            exact_notice, identity_id=identity_id,
                            context=(
                                getattr(self, "_identity_context_generation", None),
                                getattr(self, "_search_generation", None),
                                getattr(self.service, "_account_context_epoch", None),
                                getattr(self.service, "_source_context_epoch", None),
                            ),
                        )
                        if resolved_uri is not None:
                            cover_url = resolved_uri
            # A Core-local packet (valid or rejected) must NEVER cause the
            # visible row to fall back to an unqualified descriptive URL.
            # The normal BookListCell resolves valid packets through N2;
            # the invalid ones simply have no cover. No-packet historical
            # public results retain their legacy URL presentation.
            if item.get("_exact_local_cover_evidence") is not None:
                cover_url = None
            is_c = item.get("source") == "notice_c_public"
            if is_c:
                # The C row already carries a persisted exact, public N2
                # DocumentRef. The URI remains ABSENT: never turn a hot N3
                # image into the documentary proof for this list cell.
                cover_url = None
                status = (
                    "Notice C publique · à actualiser"
                    if (item.get("metadata") or {}).get("notice_c_stale")
                    else "Notice C publique · index partiel"
                )
                from cifereca.core.notice_c_loaded_filter import (
                    matching_source_label, matching_source_reason,
                )
                reason = matching_source_reason(item, state.text, field=state.field)
                summary = " · ".join(x for x in (
                    status + matching_source_label(item), reason,
                ) if x)
            else:
                summary = " · ".join(x for x in [
                    self._library_state_label(item),
                    local_match_explanation(item, state.text, field=state.field),
                ] if x)
            view_row = BookListItem(
                key, str(item.get("title") or "Sans titre"), str(item.get("author") or ""),
                summary,
                self.service.provider_display_name(provider) if provider != "local" else "Local",
                cover_url,
            )
            if is_c:
                from cifereca.core.book_cover_projection import CoverDocumentScope
                from cifereca.core.notice_cover_repository import is_notice_cover_document
                ref = item.get("_cover_document_ref")
                if (type(ref) is CoverDocumentScope
                        and ref.kind == "public_catalogue"
                        and ref.identity_id is None
                        and is_notice_cover_document(ref)):
                    view_row._cover_document_ref = ref
                    epoch = (item.get("metadata") or {}).get("notice_c_epoch")
                    if type(epoch) is int and epoch > 0:
                        view_row._cover_public_c_epoch = epoch
                    # Filtering may copy each dict. Only the original
                    # _search_local_objects row is Core's source witness.
                    from cifereca.gui.search_known_public_c_guard import (
                        attach_filtered_known_public_c,
                    )
                    attach_filtered_known_public_c(self, view_row, item)
            # Search-local only reuses an explicit Core owner+Artifact+
            # Placement cover packet. An identical URL/title/ISBN/SHA does
            # not let a catalogue notice or another identity borrow N2.
            # Import inside this method: headless adapters replay its AST
            # without module imports or GTK runtime.
            from cifereca.core.local_cover_evidence import VerifiedLocalCoverEvidence
            proof = item.get("_exact_local_cover_evidence")
            owner = getattr(
                getattr(self.service, "current_identity", None),
                "identity_id", None,
            )
            if (type(proof) is VerifiedLocalCoverEvidence
                    and proof.scope.identity_id == owner
                    and proof.scope.identity_id == item.get("identity_id")
                    and proof.scope.source_key == item.get("key")
                    and proof.scope.artifact_id == item.get("artifact_id")
                    and proof.scope.placement_id == item.get("placement_id")):
                view_row._cover_local_evidence = proof
                view_row._cover_document_ref = proof.scope
            local_rows.append(view_row)
        self._attach_row_facets(local_rows, self._search_local_objects, self.search_controls)
        for row in local_rows:
            row._public_row_content = (
                "search", row.key, getattr(row, "title", ""),
                getattr(row, "subtitle", ""), getattr(row, "meta", ""),
                getattr(row, "badge", ""), getattr(row, "action_label", ""),
                getattr(row, "action_icon_name", ""),
                bool(getattr(row, "action_callback", None)),
                getattr(
                    getattr(row, "_cover_known_public_c_receipt", None),
                    "context", None,
                ),
                getattr(row, "_cover_public_c_epoch", None),
                getattr(row, "thumbnail_url", None),
            )
            proof = getattr(row, "_cover_local_evidence", None)
            document_ref = getattr(row, "_cover_document_ref", None)
            if proof is not None or document_ref is not None:
                # The URI remains LAST; rebinding a different exact N2 ref
                # must invalidate a recycled GTK cell even if title/URL match.
                signature = row._public_row_content
                row._public_row_content = (
                    *signature[:-1],
                    proof if proof is not None else document_ref,
                    signature[-1],
                )
        self.search_page.show_rows(local_rows)
        self._search_local_snapshot_dirty = False
        shown = len(local_rows)
        self._update_search_count(shown)
        return shown
    from cifereca.gui.offer_actions import requires_selected_offer_check, download_action_label

    identity = self.service.current_identity
    identity_id = getattr(identity, "identity_id", None)
    def project(item):
        return result_row(item)
    # Only an already-present public C preview avoids the historical GTK
    # placement read; other (including empty/error) Search paths keep ABI.
    # This prevents redundant synchronous SQL while showing C before HTTP.
    known_preview = getattr(self, "_search_local_objects", None)
    has_c_preview = (
        type(known_preview) is dict
        and any(type(v) is dict and v.get("source") == "notice_c_public"
                for v in known_preview.values())
    )
    linked = (
        self.service.local_placements_for_sources(identity.identity_id)
        if identity and (self._remote_search_results or not has_c_preview) else {}
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
    # Online extension keeps already-loaded public C cards until their
    # EXACT live source is available; a provider URL/title/ISBN/record alone
    # is not evidence that an old C row can be replaced. No GTK source read.
    from cifereca.core.book_cover_projection import CoverDocumentScope
    from cifereca.core.notice_cover_repository import is_notice_cover_document
    remote_c_refs = {
        ref for item in self._remote_search_results
        if (item.source_credential == "__public__"
            and item.source_collection == "public_catalog"
            and item.source_account_id is None
            and item.owned is False and item.downloadable is False
            and item.download_url is None)
        if (type(ref := getattr(item, "_core_notice_cover_document_ref", None))
            is CoverDocumentScope
            and ref.kind == "public_catalogue"
            and ref.identity_id is None
            and is_notice_cover_document(ref))
    }
    # Compare only Core-attested same-call source and edition coordinates.
    # This avoids a duplicate C-first card after its live provider returns.
    # The projection is pure: no SQLite or HTTP on the GTK thread.
    from cifereca.core.notice_c_known_projection import matching_live_public_canonical_ref
    live_identity = getattr(
        getattr(self.service, "current_identity", None), "identity_id", None,
    )
    if type(live_identity) is str and live_identity:
        remote_c_refs.update(
            canonical for item in self._remote_search_results
            if (canonical := matching_live_public_canonical_ref(
                item, identity_id=live_identity,
            )) is not None
        )
    from cifereca.core.notice_c_loaded_filter import (
        matching_source_label, matching_source_reason,
    )
    ui_rows: list[BookListItem] = []
    for known in state.filter(self._search_local_objects.values()):
        if known.get("source") == "notice_c_public":
            document = known.get("_cover_document_ref")
            if (type(document) is CoverDocumentScope
                    and document.kind == "public_catalogue"
                    and document.identity_id is None
                    and is_notice_cover_document(document)):
                if document in remote_c_refs:
                    continue
                view = BookListItem(
                    str(known["key"]), str(known.get("title") or "Sans titre"),
                    str(known.get("author") or ""),
                    "Notice C publique · "
                    + ("à actualiser" if (known.get("metadata") or {}).get(
                        "notice_c_stale") else "index partiel")
                    + matching_source_label(known)
                    + ((" · " + reason) if (reason := matching_source_reason(
                        known, state.text, field=state.field,
                    )) else ""),
                    self.service.provider_display_name(str(known.get(
                        "provider_id") or "local")), None,
                )
                view._cover_document_ref = document
                # The canonical C can select newer candidates without
                # changing its opaque edition DocumentRef or visible text.
                # Repaint the cell rather than retaining old N4 pixels.
                epoch = (known.get("metadata") or {}).get("notice_c_epoch")
                if type(epoch) is int and epoch > 0:
                    view._cover_public_c_epoch = epoch
                from cifereca.gui.search_known_public_c_guard import (
                    attach_filtered_known_public_c,
                )
                attach_filtered_known_public_c(self, view, known)
                ui_rows.append(view)
            # An invalid C ref never falls back to a direct image URL or
            # opens a private provider action during online extension.
            continue
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
            {**project(item), "key": key} for key, item in remote_rows
        ])
    }
    from cifereca.gui.search_document_cover_bridge import (
        attach_search_document, exact_remote_search_document,
    )
    for key, item in remote_rows:
        if key not in filtered: continue
        # #4276: the versioned documentary evidence takes precedence over
        # historical book-only labels, without inventing rights or formats.
        from cifereca.document_classification import DocumentClassification
        from cifereca.core.document_filter import search_medium_label
        classification = getattr(item, "document_classification", None)
        if isinstance(classification, DocumentClassification):
            medium = search_medium_label(classification)
        else:
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
        # Documentary scope is not ownership or local presence.
        if (item.source_credential == "__public__"
                and item.source_collection == "public_catalog"):
            extras.append("Catalogue public · aucun achat attesté")
        elif item.source_collection == "library" and not stale_known:
            extras.append("Notice de bibliothèque · fichier local non présumé")
        meta = " · ".join(x for x in [medium, *extras, item.published_at] if x)
        # An offline public result is never evidence of a current entitlement,
        # a download offer or a verified local placement.
        placement_key = (item.source_provider, item.source_credential, item.record_id)
        placement = None if (stale_known or placement_key in ambiguous) else linked.get(
            placement_key
        )
        # Catalogue-only notices are not download offers, regardless of the
        # supplier name or malformed owned/downloadable flags. A real public
        # offer must be explicit before consulting Core's read-only broker.
        from cifereca.gui.offer_actions import (
            private_notice_download_hint_allowed,
            public_catalogue_download_hint_allowed,
        )
        private_hint_ok = (public_catalogue_download_hint_allowed(item)
                           and private_notice_download_hint_allowed(
            item, service=self.service, identity_id=identity_id,
        ))
        direct = (
            False if (stale_known or not private_hint_ok)
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
        # A detail may have supplied a safe image after this exact edition
        # was rendered. This is a presentation-only, scoped cache: reject
        # same-key results from another source object, profile/epoch or query.
        override = getattr(self, "_search_detail_cover_by_key", {})
        retained = override.get(key) if type(override) is dict else None
        cover_url = item.thumbnail_url
        if (type(retained) is tuple and len(retained) == 7
                and retained[0] is item
                and retained[2:] == (
                    item.medium,
                    getattr(self, "_identity_context_generation", None),
                    getattr(self, "_search_generation", None),
                    getattr(self.service, "_account_context_epoch", None),
                    getattr(self.service, "_source_context_epoch", None),
                )):
            cover_url = retained[1]
        # N2: retain a *same-source* holdings cover when the row object is
        # rebuilt after metadata enrichment or filter changes. Scope includes
        # provider, credential, record, docbase, medium, source/account/revision and
        # all four current query/identity/source generations. A title or ISBN
        # never joins two Search entries; no provider requests are issued.
        bridge = getattr(self, "_search_cover_bridge", None)
        if bridge is not None:
            from cifereca.gui.search_cover_bridge import SearchCoverBridge
            if isinstance(bridge, SearchCoverBridge) and type(identity_id) is str:
                resolved_uri = bridge.selected_url(
                    item, identity_id=identity_id,
                    context=(
                        getattr(self, "_identity_context_generation", None),
                        getattr(self, "_search_generation", None),
                        getattr(self.service, "_account_context_epoch", None),
                        getattr(self.service, "_source_context_epoch", None),
                    ),
                )
                if resolved_uri is not None:
                    cover_url = resolved_uri
        row = BookListItem(
            key, item.title, item.author or "", meta, source, cover_url,
            action_label="Ouvrir" if placement else (download_action_label(item, check_required=check_required) if direct else None),
            action_callback=callback,
            action_icon_name="document-open-symbolic" if placement else ("document-save-symbolic" if direct else None),
        )
        proof = exact_remote_search_document(self, item, key)
        if proof is not None:
            attach_search_document(row, proof)
        ui_rows.append(row)
    objects = {
        **self._search_local_objects,
        **{key: project(item) for key, item in remote_rows},
    }
    self._attach_row_facets(ui_rows, objects, self.search_controls)
    # MasterDetailPage may preserve same SourceKeys in append-fast mode; it
    # must replace a row when its title, action context or cover changed.
    for row in ui_rows:
        proof = getattr(row, "_cover_search_evidence", None)
        row._public_row_content = (
            "search", row.key, getattr(row, "title", ""),
            getattr(row, "subtitle", ""), getattr(row, "meta", ""),
            getattr(row, "badge", ""), getattr(row, "action_label", ""),
            getattr(row, "action_icon_name", ""),
            bool(getattr(row, "action_callback", None)),
            # Keep the exact source witness at [-2]: the existing
            # Search holdings publisher updates this slot in-place after
            # a verified detail response (no full list rebuild). Scope
            # generations belong BEFORE that stable slot.
            getattr(
                getattr(row, "_cover_known_public_c_receipt", None),
                "context", None,
            ),
            getattr(row, "_cover_public_c_epoch", None),
            (proof.row_signature if proof is not None else
             getattr(row, "_cover_document_ref", None)),
            getattr(row, "thumbnail_url", None),
        )
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
    response_facets = getattr(self, "_search_response_facets", None)
    if getattr(self, "search_controls", None) is not None:
        state = self.search_controls.state()
        identity = getattr(self.service, "current_identity", None)
        context = (state.text, state.field, state.scope,
                   getattr(identity, "identity_id", None))
        if response_facets is not None:
            response_facets.refresh(context, state.providers)
        if (self._search_mode == "global"
                and getattr(self, "_online_source_context", None) == context):
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
        label.set_tooltip_text(tooltip or (
            "Nombre de résultats affichés dans le connu ; index potentiellement incomplet. "
            "Les filtres affinent les résultats chargés. Étendre en ligne interroge les catalogues distants."
            if self._search_mode == "local" else
            "Nombre de résultats affichés après extension ; la couverture des catalogues peut être incomplète."
        ))

