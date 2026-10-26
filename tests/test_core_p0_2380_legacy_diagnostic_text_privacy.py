"""#3133: the compatibility collector and local text export never mask."""
from __future__ import annotations

from cifereca.diagnostics import Diagnostics, redact
from cifereca.core.diagnostic_event_feed import query_diagnostic_events


def test_known_private_keys_are_retained_verbatim(tmp_path):
    d = Diagnostics(level="detailed")
    detail = {
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
    d.add("NUMILOG", "acquisition_delivery", "failed", detail=detail, level="detailed")
    snapshot, = d.snapshot()
    assert snapshot["detail"] == detail
    exported = d.text("synthetic")
    for field, value in detail.items():
        assert str(value) in exported
        assert f"{field}=" in exported
    broker = query_diagnostic_events(
        d, log_path=tmp_path / "missing", source="core_events",
    )
    assert broker["events"][0]["detail"]["path_kind"] == "confirmation_page"


def test_untrusted_status_remains_normalized(tmp_path):
    d = Diagnostics()
    d.add("CORE", "sync source", "CUSTOM_STATUS")
    d.add("CORE", "sync source", "error")
    snapshot = d.snapshot()
    assert [row["status"] for row in snapshot] == ["unknown", "error"]
    exported = d.text("synthetic")
    assert "status=unknown" in exported and "status=error" in exported
    assert {row["status"] for row in query_diagnostic_events(
        d, log_path=tmp_path / "missing", source="core_events"
    )["events"]} == {"unknown", "error"}


def test_legacy_text_preserves_arbitrary_string_field_names():
    d = Diagnostics()
    unusual = "https://private.invalid/LOAN/KEY_987"
    d.add("CORE", "health check", detail={unusual: 5, "normal_count": 2})
    exported = d.text("synthetic", {
        "https://private.invalid/LOAN/EXTRA_KEY_987": "fixed",
        "plugins": 2,
    })
    assert unusual + "=5" in exported
    assert "https://private.invalid/LOAN/EXTRA_KEY_987: fixed" in exported
    assert "normal_count=2" in exported
    assert "plugins: 2" in exported


def test_collector_keeps_raw_identifiers_and_titles():
    raw = {
        "loan_id": "LIVER_PRIVATE_VALUE",
        "account_id": "NOT_A_EMAIL_PRIVATE_VALUE",
        "title": "Readable book title",
        "attempted": 2, "path_kind": "confirmation_page", "downloaded": 1,
    }
    assert redact(raw) == raw
    d = Diagnostics()
    d.add("CORE", "health check", detail=raw)
    assert d.snapshot()[0]["detail"] == raw


def test_presence_flags_and_identifiers_are_retained():
    d = Diagnostics()
    raw = {
        "adept_loan_id_present": True,
        "origin_profile_id_present": False,
        "loan_id": "PRIVATE_IDENTIFIER",
    }
    d.add("ADEPT", "binding evidence", detail=raw)
    assert d.snapshot()[0]["detail"] == raw


def test_hostile_plugin_objects_are_not_stringified():
    class Hostile:
        def __str__(self):
            raise AssertionError("must not stringify plugin-owned object")

        def __repr__(self):
            raise AssertionError("must not repr plugin-owned object")

        def __bool__(self):
            raise AssertionError("must not evaluate plugin-owned object")

        def __eq__(self, _other):
            raise AssertionError("must not compare plugin-owned object")

        __hash__ = object.__hash__

    d = Diagnostics(level="errors")
    d.add(Hostile(), Hostile(), Hostile(), duration_ms=Hostile(),
          detail={Hostile(): "VALUE"}, level="errors")
    record, = d.snapshot()
    assert record["category"] == "DIAGNOSTICS"
    assert record["operation"] == "[opération masquée]"
    assert record["status"] == "unknown"
    assert record["duration_ms"] is None
    assert record["detail"] == {}
    assert "EXTRA" not in d.text("synthetic", {Hostile(): "EXTRA"})


def test_raw_collector_retains_nested_string_keys_and_values():
    class Opaque:
        def __str__(self):
            raise AssertionError("must not stringify")

    nested = {
        "https://private.invalid/LOAN/RAW_ID_987": "RAW_VALUE_987",
        "safe_counter": 2,
        "private_object": Opaque(),
    }
    d = Diagnostics()
    d.add("CORE", "health check", "warning", detail={
        "nested": nested,
        "credential_id": "NOT_RECOGNISABLE_AS_SECRET_987",
    })
    raw = d.snapshot()[0]["detail"]
    assert raw["nested"] == {
        "https://private.invalid/LOAN/RAW_ID_987": "RAW_VALUE_987",
        "safe_counter": 2, "private_object": None,
    }
    assert raw["credential_id"] == "NOT_RECOGNISABLE_AS_SECRET_987"
    text = d.text("synthetic")
    assert "RAW_ID_987" in text and "RAW_VALUE_987" in text
    assert "NOT_RECOGNISABLE_AS_SECRET_987" in text
