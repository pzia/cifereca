"""#4552/#6168: Notice C cover is the authority; N2 is derived, N3 shared."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core.book_cover_projection import resolve_book_cover
from cifereca.core.notice_cover_projection import (
    notice_cover_document_ref, project_notice_cover_attribute,
)
from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_n2_adapter import NoticeCoverN2Adapter


URL = "https://covers.example.org/edition.jpg"
OTHER = "https://covers.example.org/other.jpg"
PUBLIC = NoticeSourceKey(
    provider_id="bnf", visibility="public", identity_id=None,
    credential_id=None, account_id=None, branch_id="catalogue",
    record_id="ark:42", edition_id="edition:1",
    manifestation_role="digital",
)
PRIVATE = replace(
    PUBLIC, visibility="private", identity_id="alice",
    credential_id="cred-a", account_id="borrower-a",
)


def notice(
    url=URL, *, key=PUBLIC, status="complete", parser_version="1",
    observed_at=1,
):
    return admit_source_notice(
        key=key, parser_id="catalogue-parser",
        parser_version=parser_version, observed_at=observed_at,
        raw_fields={"media": {"cover": url}, "title": "Identical title"},
        status=status,
    )


def attr(observation, *, complete=True):
    return project_notice_cover_attribute(
        observation, field_path=("media", "cover"),
        cover_inventory_complete=complete,
    )


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    media_calls = []
    pending = []

    def load(url, callback, **_kwargs):
        media_calls.append(url)
        pending.append(callback)
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr(loader, "load", load)
    service = DocumentCoverService(loader)
    adapter = NoticeCoverN2Adapter()
    try:
        yield service, adapter, loader, media_calls, pending
    finally:
        adapter.close()
        service.close()
        loader.close()


def test_public_notice_shared_across_profiles_and_time_without_n2_partition(gateway):
    service, adapter, _, calls, _ = gateway
    a = notice()
    b = notice(observed_at=12)
    assert a.source_revision == b.source_revision
    assert notice_cover_document_ref(a.key).identity_id is None
    document = adapter.register(
        service, a, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    request = service.get_cover(document, "detail", lambda _: None)
    assert request.source_uri == URL
    first = adapter._published[document].revision
    assert adapter.register(
        service, b, field_path=("media", "cover"),
        cover_inventory_complete=True,
    ) == document
    assert adapter._published[document].revision == first
    assert request.still_current
    assert service.get_cover(document, "detail", lambda _: None).source_uri == URL
    assert service.decisions.peek(document, revision=first).tier == "catalog"
    # Both viewers bind the SAME N2 decision; physical N3 remains the single
    # shared CoverLoader, rather than an identity-scoped filename.
    assert calls == [URL, URL]


def test_private_scope_is_provenance_not_a_physical_image_partition():
    a = attr(notice(key=PRIVATE))
    b = attr(notice(key=replace(
        PRIVATE, identity_id="bob", credential_id="cred-b",
        account_id="borrower-b",
    )))
    assert a.document != b.document
    assert a.document.identity_id == "alice"
    assert b.document.identity_id == "bob"
    assert a.candidates[0].url == b.candidates[0].url
    assert a.candidates[0].tier == "catalog"
    assert a.document.source_key != b.document.source_key


def test_same_url_not_a_documentary_join_for_different_editions(gateway):
    service, adapter, _, _, _ = gateway
    one = notice()
    two = notice(key=replace(PUBLIC, edition_id="edition:2"))
    a = adapter.register(
        service, one, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    b = adapter.register(
        service, two, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    assert a != b
    assert attr(one).source_revision != attr(two).source_revision
    assert service.get_cover(a, "detail", lambda _: None).source_uri == URL
    assert service.get_cover(b, "detail", lambda _: None).source_uri == URL


@pytest.mark.parametrize("value", [None, [], ""])
def test_missing_requires_complete_cover_inventory(value):
    whole = attr(notice(value))
    incomplete = attr(notice(value), complete=False)
    assert incomplete.state == "pending"
    assert not incomplete.snapshot_complete
    if value != "":
        assert whole.state == "missing"
        assert whole.snapshot_complete
    else:
        assert whole.state == "pending"


def test_partial_observation_never_tombstones_even_with_an_attested_url(gateway):
    service, adapter, _, calls, _ = gateway
    partial = notice(status="partial")
    attribute = attr(partial)
    assert attribute.state == "pending"
    assert len(attribute.candidates) == 1
    document = adapter.register(
        service, partial, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    request = service.get_cover(document, "detail", lambda _: None)
    assert request.source_uri is None
    assert service.decisions.peek(document, revision=1).status == "pending"
    assert not calls


def test_two_same_tier_covers_are_ambiguous_without_an_arbitrary_tie_break(gateway):
    service, adapter, _, calls, _ = gateway
    observed = notice([URL, OTHER])
    attribute = attr(observed)
    assert attribute.state == "available"
    assert resolve_book_cover(
        attribute.document, attribute.candidates, revision=1,
        snapshot_complete=attribute.snapshot_complete,
    ).status == "ambiguous"
    document = adapter.register(
        service, observed, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    assert service.get_cover(document, "detail", lambda _: None).source_uri is None
    assert not calls


@pytest.mark.parametrize("unsafe", [
    "file:///home/user/private-cover.png",
    "http://covers.example.org/cover.jpg",
    "https://127.0.0.1/cover.jpg",
    "ftp://covers.example.org/not-a-safe-image.jpg",
    1234,
    ["https://covers.example.org/ok.jpg", "file:///etc/passwd"],
])
def test_unadmitted_media_is_pending_not_missing_or_downloaded(unsafe):
    observation = notice(unsafe)
    attribute = attr(observation)
    assert attribute.state == "pending"
    assert attribute.candidates == ()
    assert not attribute.snapshot_complete


def test_version_change_invalidates_only_n2_and_rejects_delayed_old_callback(gateway):
    service, adapter, loader, calls, pending = gateway
    first = notice()
    document = adapter.register(
        service, first, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    painted = []
    old = service.get_cover(document, "detail", painted.append)
    assert old.still_current and calls == [URL]
    cached_path = loader._path(URL)

    changed = notice(OTHER)
    adapter.register(
        service, changed, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    assert not old.still_current
    assert service.get_cover(document, "detail", lambda _: None).source_uri == OTHER
    # Same URL again after A->B->A must not revive the old N2 token.
    adapter.register(
        service, first, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    assert not old.still_current
    assert service.get_cover(document, "detail", lambda _: None).source_uri == URL
    pending[0](Path("/tmp/old-callback.png"))
    assert painted == []
    assert loader._path(URL) == cached_path
    assert adapter._published[document].revision == 3


def test_parser_revision_and_field_mapping_change_requalify_n2(gateway):
    service, adapter, _, _, _ = gateway
    first = notice()
    document = adapter.register(
        service, first, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    adapter.register(
        service, notice(parser_version="2", observed_at=2),
        field_path=("media", "cover"),
        cover_inventory_complete=True,
    )
    assert adapter._published[document].revision == 2
    # A newer *incomplete* mapping is no evidence that the last verified
    # cover disappeared. Notice C must retain the old N2 token and advance
    # only the out-of-order observation watermark.
    adapter.register(
        service, notice(observed_at=3), field_path=("different", "field"),
        cover_inventory_complete=False,
    )
    assert adapter._published[document].revision == 2
    assert service.get_cover(document, "detail", lambda _: None).source_uri == URL
    # Only a subsequent *complete* inventory can attest that the newly
    # selected parser field has no cover; then N2 must requalify to missing.
    adapter.register(
        service, notice(observed_at=4), field_path=("different", "field"),
        cover_inventory_complete=True,
    )
    assert adapter._published[document].revision == 3
    assert service.get_cover(document, "detail", lambda _: None).source_uri is None


def test_forged_revision_and_embedded_without_placement_are_refused(gateway):
    service, adapter, _, _, _ = gateway
    source = notice()
    tampered = replace(source, source_revision="notice-raw.v1:" + "0" * 64)
    with pytest.raises(ValueError, match="invalid_cover_notice_observation"):
        attr(tampered)
    with pytest.raises(ValueError, match="invalid_cover_notice_observation"):
        adapter.register(
            service, replace(source, raw_json='{"media":{"cover":"https://changed.example/x"}}'),
            field_path=("media", "cover"), cover_inventory_complete=True,
        )
    embedded = replace(
        PRIVATE, visibility="embedded", credential_id=None,
        account_id=None, record_id="sha256:" + "a" * 64,
    )
    with pytest.raises(ValueError, match="exact_catalogue_source"):
        notice_cover_document_ref(embedded)


def test_invalid_field_path_and_closed_bridge_refuse_registration(gateway):
    service, adapter, _, _, _ = gateway
    with pytest.raises(ValueError, match="field_path"):
        project_notice_cover_attribute(
            notice(), field_path=(), cover_inventory_complete=True,
        )
    with pytest.raises(TypeError, match="explicit"):
        project_notice_cover_attribute(
            notice(), field_path=("media", "cover"),
            cover_inventory_complete=1,
        )
    adapter.close()
    with pytest.raises(RuntimeError, match="closed"):
        adapter.register(
            service, notice(), field_path=("media", "cover"),
            cover_inventory_complete=True,
        )
