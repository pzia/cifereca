"""P1 #6168: no silent URL fallback after an unqualified N3 EPUB failure."""
from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.epub_cover_materialization import ExactEpubCoverResource
from cifereca.core.epub_cover_failure_qualification import (
    EpubCoverPhysicalAssessment, qualify_failed_epub_cover,
)
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def attested(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "book.epub"
    payload = _write_epub(book, epub3=True)
    sha = hashlib.sha256(payload).hexdigest()
    metadata = epub_metadata.inspect_epub(book)
    assert metadata["epub_cover_status"] == "extracted"
    resource = ExactEpubCoverResource(
        scope=CoverDocumentScope(
            kind="placement", identity_id="owner", source_key="book:1",
            artifact_id="sha256:" + sha, placement_id="place:1",
        ),
        source_revision=5,
        artifact_sha256=sha,
        member=metadata["epub_cover"]["member"],
        cache_name=metadata["epub_cover"]["cache_name"],
    )
    image = epub_metadata.cover_cache_dir() / resource.cache_name
    assert epub_metadata.valid_cached_cover_path(image)
    return book, resource, image


def test_warm_physical_cache_does_not_open_original_epub(attested):
    book, resource, _image = attested
    book.unlink()
    assessment = qualify_failed_epub_cover(resource, source_path=book)
    assert assessment == EpubCoverPhysicalAssessment(
        resource, "available", "n3_verified",
    )
    assert not assessment.permits_url_fallback


def test_missing_image_with_valid_epub_is_retryable_not_fallback(attested):
    book, resource, image = attested
    image.unlink()
    assessment = qualify_failed_epub_cover(resource, source_path=book)
    assert assessment.state == "retryable"
    assert assessment.code == "materialization_failed"
    assert not assessment.permits_url_fallback
    assert not image.exists()


def test_modified_artifact_does_not_authorize_an_origin_url(attested):
    book, resource, image = attested
    image.unlink()
    book.write_bytes(book.read_bytes() + b"modified bytes")
    assessment = qualify_failed_epub_cover(resource, source_path=book)
    assert assessment == EpubCoverPhysicalAssessment(
        resource, "retryable", "source_unverified",
    )
    assert not assessment.permits_url_fallback


def test_missing_or_ambiguous_member_in_same_immutable_epub_qualifies(attested):
    book, resource, image = attested
    image.unlink()
    altered = replace(resource, member="OPS/does-not-exist.png")
    assessment = qualify_failed_epub_cover(altered, source_path=book)
    assert assessment == EpubCoverPhysicalAssessment(
        altered, "definitive_unusable", "member_missing_or_ambiguous",
    )
    assert assessment.permits_url_fallback
    assert not image.exists()


def test_image_sha_mismatch_in_exact_epub_qualifies(attested):
    book, resource, image = attested
    image.unlink()
    altered = replace(resource, cache_name="f" * 64 + ".png")
    assessment = qualify_failed_epub_cover(altered, source_path=book)
    assert assessment.state == "definitive_unusable"
    assert assessment.code == "image_digest_mismatch"
    assert assessment.permits_url_fallback


def test_oversized_exact_cover_member_is_definitive(attested, monkeypatch):
    from cifereca.core import epub_cover_failure_qualification as quality

    book, resource, image = attested
    image.unlink()

    def oversized(_archive, _member, _limit):
        raise ValueError("oversized_entry")

    monkeypatch.setattr(quality, "_read", oversized)
    assessment = qualify_failed_epub_cover(resource, source_path=book)
    assert assessment.code == "member_unusable"
    assert assessment.permits_url_fallback


def test_untrusted_parse_failure_never_qualifies_or_leaks_path(attested, monkeypatch):
    from cifereca.core import epub_cover_failure_qualification as quality

    book, resource, image = attested
    image.unlink()

    def unavailable(_stream):
        raise ValueError("/home/private/user/book.epub")

    monkeypatch.setattr(quality, "_bounded_directory", unavailable)
    assessment = qualify_failed_epub_cover(resource, source_path=book)
    assert assessment.state == "retryable"
    assert assessment.code == "source_unverified"
    assert "/home/" not in repr(assessment)
    assert not assessment.permits_url_fallback


def test_unavailable_and_symlink_epub_never_qualify(attested, tmp_path):
    book, resource, image = attested
    image.unlink()
    alias = tmp_path / "symlink.epub"
    alias.symlink_to(book)
    assert not qualify_failed_epub_cover(
        resource, source_path=alias,
    ).permits_url_fallback
    book.unlink()
    assert not qualify_failed_epub_cover(
        resource, source_path=book,
    ).permits_url_fallback


@pytest.mark.parametrize("bad", [
    (object(), "valid"), (None, "valid"),
])
def test_rejects_unproven_resource_types(attested, bad):
    book, _resource, _image = attested
    with pytest.raises(TypeError):
        qualify_failed_epub_cover(bad[0], source_path=book)


def test_rejects_unsafe_paths_prior_to_io(attested):
    _book, resource, _image = attested
    with pytest.raises(ValueError):
        qualify_failed_epub_cover(resource, source_path="file:///tmp/nope.epub")
    with pytest.raises(ValueError):
        qualify_failed_epub_cover(resource, source_path=type(_book)("relative.epub"))
