"""#1346: Kobo diagnostics never publish arbitrary upstream rejection values.

All providers, accounts and catalogue adapters are synthetic, with no HTTP,
DRM, acquisition, disk mutation or personal profile data.
"""
import json

from cifereca_kobo import _public_kobo_rejections
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


def test_manifest_rejections_are_redacted_before_the_library_diagnostic_boundary():
    vault = FakeVault([], rejected=[
        {"type": "invalid_scope", "reason": "duplicate_product_id",
         "source": "library", "PRIVATE_KEY": "Bearer PRIVATE_TOKEN"},
        {"type": "Bearer PRIVATE_CATEGORY", "reason": "/home/PRIVATE_BOOK.epub",
         "source": "https://PRIVATE_HOST/?sig=PRIVATE", "account_id": "PRIVATE_ACCOUNT"},
        {"type": "invalid_shape", "reason": "items_not_list",
         "source": "catalog", "signed_url": "PRIVATE_DOWNLOAD"},
        ["PRIVATE_NON_DICTIONARY"],
    ])
    session = session_with_vault(vault)
    try:
        assert session.library() == []
        expected = [
            {"type": "invalid_scope", "reason": "duplicate_product_id",
             "source": "library"},
            {"type": "invalid_shape", "reason": "unverified_provider_rejection"},
            {"type": "invalid_shape", "reason": "items_not_list",
             "source": "catalog"},
            {"type": "invalid_shape", "reason": "unverified_provider_rejection"},
        ]
        assert session.last_library_rejections == expected
        assert session.diagnostic_state()["library_rejections"] == expected
        exported = json.dumps(session.diagnostic_state()["library_rejections"])
        for secret in ("PRIVATE", "Bearer", "/home/", "signed_url", "account_id",
                       "https:", "NON_DICTIONARY"):
            assert secret not in exported
        assert vault.acquisition_calls == 0
        assert vault.manifest_calls == 1
    finally:
        session.close()


def test_catalogue_rejections_are_redacted_even_if_adapter_supplies_private_fields():
    session = session_with_vault(FakeVault([]))
    try:
        session.catalog.last_search_rejections = [
            {"type": "invalid_scope", "reason": "personal_search_denied",
             "source": "library", "token": "Bearer PRIVATE_TOKEN"},
            {"type": "PRIVATE_TYPE", "reason": "PRIVATE_REASON",
             "source": "PRIVATE_SOURCE", "title": "PRIVATE_BOOK"},
        ]
        assert session.diagnostic_state()["catalog_search_rejections"] == [
            {"type": "invalid_scope", "reason": "personal_search_denied",
             "source": "library"},
            {"type": "invalid_shape", "reason": "unverified_provider_rejection"},
        ]
        assert "PRIVATE" not in json.dumps(
            session.diagnostic_state()["catalog_search_rejections"]
        )
    finally:
        session.close()


def test_untrusted_rejection_container_is_a_public_generic_failure():
    assert _public_kobo_rejections("Bearer PRIVATE_TOKEN") == [
        {"type": "invalid_shape", "reason": "unverified_rejection_list"},
    ]
    assert _public_kobo_rejections(None) == []
    assert _public_kobo_rejections(()) == []


def test_rejection_export_is_bounded_and_preserves_known_reason_codes():
    incoming = [
        {"type": "invalid_shape", "reason": "unverifiable_product_id",
         "secret": f"PRIVATE_ITEM_{index}"}
        for index in range(1000)
    ]
    safe = _public_kobo_rejections(incoming)
    assert len(safe) == 257
    assert safe[0] == {
        "type": "invalid_shape", "reason": "unverifiable_product_id",
    }
    assert safe[-1] == {"type": "invalid_shape", "reason": "truncated_rejections"}
    assert "PRIVATE" not in json.dumps(safe)
