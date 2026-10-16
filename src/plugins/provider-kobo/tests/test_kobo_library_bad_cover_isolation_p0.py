"""#1346: malformed Kobo image metadata must not abort account inventory.

All data are synthetic. No HTTP, acquisition, filesystem write, DRM or GTK.
"""
from __future__ import annotations

import json

from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


class BadCoverKey:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_COVER_KEY")


class BadTitle:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_BOOK_TITLE")


def test_poisoned_cover_keeps_independent_library_books_visible():
    vault = FakeVault([
        {"product_id": "first", "title": "First", "cover_url": "https://cdn.example/first.jpg"},
        {"product_id": "bad-cover", "title": "Private", "images": {
            BadCoverKey(): "https://cdn.example/private.jpg",
        }},
        {"product_id": "third", "title": "Third", "thumbnail_url": "https://cdn.example/third.jpg"},
    ])
    session = session_with_vault(vault)
    try:
        projected = session.library()
        assert [item.record_id for item in projected] == ["first", "third"]
        assert session.last_library_rejections == [{
            "type": "ProviderError", "reason": "normalization_error",
        }]
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
        public = json.dumps(session.last_library_rejections)
        for secret in ("PRIVATE", "Bearer", "bad-cover", "cdn.example"):
            assert secret not in public
    finally:
        session.close()


def test_malformed_catalogue_scalar_is_quarantined_without_losing_other_books():
    vault = FakeVault([
        {"product_id": "one", "title": "Safe one"},
        {"product_id": "poison", "title": BadTitle()},
        {"product_id": "two", "title": "Safe two"},
    ])
    session = session_with_vault(vault)
    try:
        assert [item.record_id for item in session.library()] == ["one", "two"]
        assert len(session.last_library_rejections) == 1
        assert session.last_library_rejections[0]["reason"] == "normalization_error"
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
        assert "PRIVATE" not in str(session.last_library_rejections)
    finally:
        session.close()


def test_advisory_backup_cover_count_cannot_abort_rights_manifest():
    vault = FakeVault([
        {"product_id": "owned", "owned": True, "downloadable": False,
         "images": {BadCoverKey(): "PRIVATE_IMAGE_URL"}},
        {"product_id": "safe", "owned": True, "downloadable": False,
         "cover_url": "https://cdn.example/safe.jpg"},
    ])
    session = session_with_vault(vault)
    try:
        manifest = session.backup_manifest()
        assert [row["product_id"] for row in manifest] == ["owned", "safe"]
        assert session.backup_manifest_rejection_count == 0
        assert session.backup_manifest_policy["manifest_rejections"] == 0
        assert session.backup_manifest_policy["acquisition_authorized"] is False
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
        exported = json.dumps(session.diagnostic_state()["manifest_materialization_policy"])
        assert "PRIVATE" not in exported
    finally:
        session.close()
