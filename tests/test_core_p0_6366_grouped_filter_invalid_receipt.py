"""A stale multi-source matching packet must not erase a known primary C.

Controlled two-source SQLite admission, then eviction and restart. No supplier
network, private identity authority, acquisition or placement.
"""
from contextlib import closing
from copy import deepcopy

from cifereca.core.document_filter import filter_loaded_with_documentary_proofs
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_loaded_filter import expand_filter_sources
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_multisource_c_search import shared
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_stale_group_packet_preserves_primary_but_never_matches_alternative(tmp_path):
    filename = tmp_path / "invalid-group.sqlite"
    with closing(Storage(filename)) as db:
        first = persist(db, verified_public(
            source_provider="bnf", record_id="primary",
            title="Groupe stable", author="Premier",
        ))
        second = persist(db, verified_public(
            source_provider="numilog-catalog", record_id="alternative",
            title="Groupe stable", author="Second",
        ))
        canonical = shared(db, first, second)
        for source in (first, second):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)

    with closing(Storage(filename)) as db:
        page = search_public_notice_c(db, "Groupe stable", group_by_document=True)
        primary, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert primary["_cover_document_ref"] == canonical.document
        expanded, _ = expand_filter_sources([primary], scope="all")
        assert len(expanded) == 2
        alternative, = (
            entry for entry in expanded
            if entry["provider_id"] != primary["provider_id"]
        )
        other_provider, other_author = alternative["provider_id"], alternative["author"]
        matched = filter_loaded_with_documentary_proofs(
            [primary], other_author, providers=(other_provider,),
        )
        assert len(matched) == 1
        assert matched[0]["key"] == primary["key"]
        assert matched[0]["_notice_c_matched_providers"] == (other_provider,)

        for corruption in ("epoch", "identity", "document", "packet"):
            row = {**primary, "metadata": deepcopy(primary["metadata"])}
            if corruption == "epoch":
                row["metadata"]["notice_c_epoch"] = True
            elif corruption == "identity":
                row["identity_id"] = "bob"
            elif corruption == "document":
                row["_cover_document_ref"] = None
            else:
                row.pop("_core_notice_c_filter_sources")
            rows, originals = expand_filter_sources([row], scope="all")
            assert rows == [row] and originals == {}
            # The primary is still searchable; the unsupported alternative
            # author/provider cannot be promoted from stale group metadata.
            assert filter_loaded_with_documentary_proofs(
                [row], "Groupe stable", providers=(primary["provider_id"],),
            ) == [row]
            assert filter_loaded_with_documentary_proofs(
                [row], other_author, providers=(other_provider,),
            ) == []
        class Hostile:
            def __eq__(self, other):
                raise AssertionError("external equality on group selection")

            def __ne__(self, other):
                raise AssertionError("external inequality on group selection")

        for key in ("source", "identity_id", "_cover_document_ref",
                    "provider_id", "item_id"):
            row = {**primary, "metadata": dict(primary["metadata"])}
            row[key] = Hostile()
            expanded, originals = expand_filter_sources([row], scope="all")
            assert expanded == [row] and originals == {}
        from dataclasses import replace
        packet = primary["_core_notice_c_filter_sources"]
        for tampered in (
            (packet[0], Hostile(), *packet[2:]),
            (*packet[:4], (replace(packet[4][0], provider_id=Hostile()), *packet[4][1:])),
            (*packet[:4], (replace(packet[4][0], record_id=Hostile()), *packet[4][1:])),
        ):
            row = {**primary, "_core_notice_c_filter_sources": tampered}
            expanded, originals = expand_filter_sources([row], scope="all")
            assert expanded == [row] and originals == {}
        from cifereca.core.notice_c_loaded_filter import (
            matching_source_label, collapse_filter_sources, is_expanded_source, _MARKER,
        )
        assert matching_source_label({
            "_notice_c_matched_providers": (other_provider,),
            "provider_id": Hostile(),
        }) == ""
        class HostileHash:
            def __hash__(self):
                raise AssertionError("foreign hash in group collapse")

            def __eq__(self, other):
                raise AssertionError("foreign equality in group collapse")

        foreign = {_MARKER: HostileHash(), "provider_id": other_provider}
        assert not is_expanded_source(foreign)
        assert collapse_filter_sources([foreign], {}) == [foreign]
        assert db.entitlements() == db.acquisitions() == db.placements() == []
