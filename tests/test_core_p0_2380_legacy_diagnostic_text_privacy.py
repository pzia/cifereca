"""Legacy diagnostics text and memory views preserve raw data (#3133).

Only the GTK Diagnostics presentation pseudonymizes explicit login/password;
compatibility Core APIs never redact on write, read or export.
"""
from __future__ import annotations

from cifereca.diagnostics import Diagnostics, redact
from cifereca.core.diagnostic_event_feed import query_diagnostic_events


def test_original_metadata_is_available_in_memory_and_legacy_text(tmp_path):
    d = Diagnostics(level="detailed")
    details = {
        "account_id": "ACCOUNT_PRIVATE_987",
        "credential_id": "CREDENTIAL_PRIVATE_987",
        "identity_id": "IDENTITY_PRIVATE_987",
        "profile_id": "PROFILE_PRIVATE_987",
        "loan_id": "LOAN_PRIVATE_987",
        "source_item_id": "PURCHASE_PRIVATE_987",
        "book_title": "TITLE_PRIVATE_987",
        "acsm": "ACSM_PRIVATE_987",
        "signed_url": "SIGNED_PRIVATE_987",
        "headers": "HEADERS_PRIVATE_987",
        "path_kind": "confirmation_page",
        "code": "http_ok", "attempted": 1,
    }
    d.add("NUMILOG", "acquisition_delivery", "failed", detail=details, level="detailed")
    snapshot, = d.snapshot()
    assert snapshot["detail"] == details
    exported = d.text("synthetic")
    for value in details.values():
        if type(value) is str:
            assert value in exported
    broker = query_diagnostic_events(d, log_path=tmp_path / "missing", source="core_events")
    # Explicit source='core_events' is a deprecated projected compatibility
    # feed; journal-backed broker calls are the raw, authoritative path.
    assert broker["events"][0]["source"] == "core_events"


def test_untrusted_status_stays_within_structured_vocabulary(tmp_path):
    d = Diagnostics()
    d.add("CORE", "sync source", "CUSTOM_STATUS")
    d.add("CORE", "sync source", "error")
    assert [row["status"] for row in d.snapshot()] == ["unknown", "error"]
    assert "status=unknown" in d.text("synthetic")
    assert {row["status"] for row in query_diagnostic_events(
        d, log_path=tmp_path / "missing", source="core_events"
    )["events"]} == {"unknown", "error"}


def test_export_preserves_free_form_dictionary_keys_and_extra_fields():
    d = Diagnostics()
    raw_key = "https://private.invalid/LOAN/KEY_987"
    extra_key = "https://private.invalid/LOAN/EXTRA_KEY_987"
    d.add("CORE", "health check", detail={raw_key: 5, "normal_count": 2})
    text = d.text("synthetic", {extra_key: "fixed", "plugins": 2})
    assert raw_key in text and extra_key in text
    assert "normal_count=2" in text and "plugins: 2" in text


def test_shared_legacy_helpers_and_collector_preserve_titles_ids_and_flags():
    original = {
        "loan_id": "LIVER_PRIVATE_VALUE", "account_id": "ACCOUNT_PRIVATE_VALUE",
        "title": "Readable book title", "attempted": 2,
        "path_kind": "confirmation_page", "downloaded": 1,
    }
    assert redact(original) is original
    d = Diagnostics()
    d.add("CORE", "health check", detail=original)
    assert d.snapshot()[0]["detail"] == original


def test_presence_flags_and_values_are_not_censored():
    d = Diagnostics()
    original = {
        "adept_loan_id_present": True, "origin_profile_id_present": False,
        "loan_id": "PRIVATE_IDENTIFIER",
    }
    d.add("ADEPT", "binding evidence", detail=original)
    assert d.snapshot()[0]["detail"] == original


def test_hostile_plugin_fields_cannot_crash_collector_or_export():
    class Hostile:
        def __str__(self):
            raise AssertionError("must not stringify plugin-owned object")
        def __bool__(self):
            raise AssertionError("must not evaluate private object")
        def __eq__(self, _other):
            raise AssertionError("must not compare private object")
        __hash__ = object.__hash__

    d = Diagnostics(level="errors")
    d.add(Hostile(), Hostile(), Hostile(), duration_ms=Hostile(),
          detail={Hostile(): "OTHER_VALUE"}, level="errors")
    record, = d.snapshot()
    assert record["category"] == "DIAGNOSTICS"
    assert record["operation"] == "[opération invalide]"
    assert record["status"] == "unknown"
    assert record["duration_ms"] is None
    assert record["detail"] == {}
    assert "Hostile" not in d.text("synthetic", {Hostile(): "EXTRA_VALUE"})


def test_nested_json_preserves_raw_keys_and_skips_opaque_values():
    class Opaque:
        def __str__(self):
            raise AssertionError("foreign object hooks forbidden")

    d = Diagnostics()
    key = "https://private.invalid/LOAN/RAW_ID_987"
    d.add("CORE", "health check", "warning", detail={
        "nested": {key: "RAW_VALUE_987", "safe_counter": 2, "opaque": Opaque()},
        "credential_id": "VISIBLE_CREDENTIAL_987",
    })
    detail = d.snapshot()[0]["detail"]
    assert detail["nested"] == {key: "RAW_VALUE_987", "safe_counter": 2, "opaque": None}
    assert detail["credential_id"] == "VISIBLE_CREDENTIAL_987"
    exported = d.text("synthetic")
    assert key in exported and "RAW_VALUE_987" in exported
