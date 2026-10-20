"""#2380: legacy text export must not reintroduce known private fields.

This checks the old Settings / DiagnosticsBroker text route in addition to
the independently redacted structured diagnostics.events broker. No provider,
file, account, LLM or clipboard operation is performed.
"""
from __future__ import annotations

from cifereca.diagnostics import Diagnostics, redact
from cifereca.core.diagnostic_event_feed import query_diagnostic_events


def test_known_private_keys_are_redacted_even_without_secret_pattern(tmp_path):
    d = Diagnostics(level="detailed")
    d.add("NUMILOG", "acquisition_delivery", "failed", detail={
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
        "path_kind": "confirmation_page",  # safe fixed Numilog enum
        "code": "http_ok", "attempted": 1,
    }, level="detailed")
    snapshot, = d.snapshot()
    assert snapshot["detail"]["path_kind"] == "confirmation_page"
    assert snapshot["detail"]["code"] == "http_ok"
    assert snapshot["detail"]["attempted"] == 1
    serialized = str(snapshot) + d.text("synthetic")
    for private in (
        "ACCOUNT_PRIVATE_987", "CREDENTIAL_PRIVATE_987",
        "IDENTITY_PRIVATE_987", "PROFILE_PRIVATE_987", "LOAN_PRIVATE_987",
        "PURCHASE_PRIVATE_987", "TITLE_PRIVATE_987", "ACSM_PRIVATE_987",
        "SIGNED_PRIVATE_987", "HEADERS_PRIVATE_987",
    ):
        assert private not in serialized
    assert all(snapshot["detail"][field] == "<redacted>" for field in (
        "account_id", "credential_id", "identity_id", "profile_id",
        "loan_id", "source_item_id", "book_title", "acsm", "signed_url",
        "headers",
    ))
    broker = query_diagnostic_events(d, log_path=tmp_path / "missing",
                                     source="core_events")
    assert broker["events"][0]["detail"]["path_kind"] == "confirmation_page"


def test_untrusted_status_does_not_bypass_structured_status_vocabulary(tmp_path):
    d = Diagnostics()
    d.add("CORE", "sync source", "PRIVATE_CUSTOMER_UNRECOGNISED")
    d.add("CORE", "sync source", "error")
    snapshot = d.snapshot()
    assert [row["status"] for row in snapshot] == ["unknown", "error"]
    exported = d.text("synthetic")
    assert "PRIVATE_CUSTOMER_UNRECOGNISED" not in exported
    assert "status=unknown" in exported and "status=error" in exported
    assert {row["status"] for row in query_diagnostic_events(
        d, log_path=tmp_path / "missing", source="core_events"
    )["events"]} == {"unknown", "error"}


def test_legacy_text_output_masks_hostile_dictionary_field_names():
    d = Diagnostics()
    d.add("CORE", "health check", "ok", detail={
        "https://private.invalid/LOAN/KEY_987": 5,
        "normal_count": 2,
    })
    text = d.text("synthetic", {
        "https://private.invalid/LOAN/EXTRA_KEY_987": "fixed",
        "plugins": 2,
    })
    assert "KEY_987" not in text and "EXTRA_KEY_987" not in text
    assert "normal_count=2" in text
    assert "plugins: 2" in text
    assert "[champ masqué]" in text


def test_collector_private_keys_do_not_change_shared_book_ui_redaction():
    raw = {
        "loan_id": "LIVER_PRIVATE_VALUE",
        "account_id": "NOT_A_EMAIL_PRIVATE_VALUE",
        "title": "Readable book title",
        "attempted": 2,
        "path_kind": "confirmation_page",
        "downloaded": 1,
    }
    # Public redaction is also used by the Synchronisation book-title UI.
    assert redact(raw) == raw
    d = Diagnostics()
    d.add("CORE", "health check", detail=raw)
    protected = d.snapshot()[0]["detail"]
    for key in ("loan_id", "account_id", "title"):
        assert protected[key] == "<redacted>"
    assert protected["attempted"] == 2
    assert protected["path_kind"] == "confirmation_page"
    assert protected["downloaded"] == 1


def test_presence_flags_are_not_mistaken_for_private_identifiers():
    d = Diagnostics()
    d.add("ADEPT", "binding evidence", detail={
        "adept_loan_id_present": True,
        "origin_profile_id_present": False,
        "loan_id": "PRIVATE_IDENTIFIER",
    })
    detail = d.snapshot()[0]["detail"]
    assert detail["adept_loan_id_present"] is True
    assert detail["origin_profile_id_present"] is False
    assert detail["loan_id"] == "<redacted>"


def test_hostile_plugin_fields_cannot_crash_collector_or_text_export():
    class Hostile:
        def __str__(self):
            raise AssertionError("must not stringify plugin-owned object")

        def __bool__(self):
            raise AssertionError("must not evaluate private object")

        def __eq__(self, _other):
            raise AssertionError("must not compare private object")

        __hash__ = object.__hash__

    d = Diagnostics(level="errors")
    # Invalid free fields must not be evaluated when filtering by severity.
    d.add(Hostile(), Hostile(), Hostile(), duration_ms=Hostile(),
          detail={Hostile(): "PRIVATE_KEY_VALUE"})
    record, = d.snapshot()
    assert record["category"] == "DIAGNOSTICS"
    assert record["operation"] == "[opération masquée]"
    assert record["status"] == "unknown"
    assert record["duration_ms"] is None
    assert record["detail"] == {}
    assert "PRIVATE_KEY_VALUE" not in d.text("synthetic",
        {Hostile(): "PRIVATE_EXTRA_VALUE"})
    assert "PRIVATE_EXTRA_VALUE" not in d.text("synthetic",
        {Hostile(): "PRIVATE_EXTRA_VALUE"})


def test_an_invalid_field_name_also_hides_the_field_value():
    d = Diagnostics()
    d.add("CORE", "health check", detail={
        "https://private.invalid/PRIVATE_NAME": "PRIVATE_VALUE",
    })
    exported = d.text("synthetic", {
        "alice@example.invalid": "PRIVATE_EXTRA_VALUE",
    })
    assert "PRIVATE_NAME" not in exported
    assert "PRIVATE_VALUE" not in exported
    assert "PRIVATE_EXTRA_VALUE" not in exported
    assert "[champ masqué]" in exported
