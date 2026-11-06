"""#4552: known input bytes vs source changes, no network or raw values."""
from dataclasses import replace

import pytest

from cifereca.core.notice_parse_witness import (
    CriticalParseField, ParseWitness, field_digest, review_parse_witness,
)


SCOPE = "1" * 64
CONTENT = "2" * 64
TITLE = "Étude du réel"


def witness(fields=None):
    return ParseWitness(
        source_scope_sha256=SCOPE, approved_input_sha256=CONTENT,
        parser_version="1.0.0", approved=True,
        critical_fields=fields or (
            CriticalParseField(("title",), "string"),
            CriticalParseField(("metadata", "edition"), "string"),
        ),
    )


def review(**changes):
    return review_parse_witness(
        changes.get("baseline", witness()),
        source_scope_sha256=changes.get("scope", SCOPE),
        input_sha256=changes.get("input_hash", CONTENT),
        parser_version=changes.get("version", "1.1.0"),
        raw_fields=changes.get(
            "raw", {"title": TITLE, "metadata": {"edition": "EPUB"}},
        ),
        complete=changes.get("complete", True),
    )


def test_identical_response_and_new_parser_missing_critical_field_is_confirmed():
    observed = review(raw={"metadata": {"edition": "EPUB"}})
    assert observed.status == "parser_regression_confirmed"
    assert observed.affected_fields == ("critical_field_1",)
    assert observed.executable is False
    assert TITLE not in repr(observed)


def test_same_input_and_parser_without_loss_is_unchanged():
    value = review()
    assert value.status == "unchanged" and value.affected_fields == ()


@pytest.mark.parametrize("change", [
    {"input_hash": "3" * 64, "raw": {}},
    {"input_hash": "3" * 64, "raw": {"title": "Autre édition"}},
])
def test_source_bytes_changed_never_confirm_parser_bug(change):
    value = review(**change)
    assert value.status == "source_changed"
    assert value.affected_fields == ()


@pytest.mark.parametrize("change", [
    {"complete": False, "raw": {}},
    {"scope": "f" * 64, "raw": {}},
    {"input_hash": None, "raw": {}},
    {"baseline": replace(witness(), approved=False), "raw": {}},
])
def test_unapproved_partial_or_foreign_input_is_unknown_not_confirmed(change):
    value = review(**change)
    assert value.status == "unverified"
    assert value.affected_fields == ()


def test_same_parser_same_bytes_but_missing_field_is_suspected_not_confirmed():
    r = review(version="1.0.0", raw={"title": TITLE})
    assert r.status == "parser_drift_suspected"
    assert r.affected_fields == ("critical_field_2",)


def test_optional_field_missing_is_not_a_regression():
    b = witness((
        CriticalParseField(("title",), "string"),
        CriticalParseField(("metadata", "edition"), "string", required=False),
    ))
    output = review(baseline=b, raw={"title": TITLE})
    assert output.status == "unchanged"


def test_optional_present_field_with_wrong_type_is_regression():
    b = witness((CriticalParseField(("metadata", "edition"), "string",
                                    required=False),))
    output = review(baseline=b, raw={"metadata": {"edition": 12}})
    assert output.status == "parser_regression_confirmed"


def test_pinned_stable_value_digest_detects_parser_change_with_same_input():
    b = witness((CriticalParseField(
        ("title",), "string", expected_sha256=field_digest(TITLE),
    ),))
    assert review(baseline=b, raw={"title": TITLE}).status == "unchanged"
    altered = review(baseline=b, raw={"title": "Lost spacing"})
    assert altered.status == "parser_regression_confirmed"
    assert altered.affected_fields == ("critical_field_1",)


def test_opaque_field_codes_never_expose_values_or_source_coordinates():
    b = witness((CriticalParseField(("metadata", "privateField"), "string"),))
    r = review(baseline=b, raw={"metadata": {"privateField": 1}})
    assert r.affected_fields == ("critical_field_1",)
    assert "privateField" not in repr(r)
    assert SCOPE not in repr(r)
    assert TITLE not in repr(r)


@pytest.mark.parametrize("bad", [
    replace(witness(), critical_fields=()),
    replace(witness(), approved="true"),
    replace(witness(), source_scope_sha256=7),
    replace(witness(), approved_input_sha256="not-a-sha"),
    replace(witness(), parser_version="bad version"),
    replace(witness(), critical_fields=(CriticalParseField(("title",), "string"),) * 2),
    replace(witness(), critical_fields=(CriticalParseField(("title",), "string"),) * 33),
    replace(witness(), critical_fields=(CriticalParseField(("bad key",), "string"),)),
    replace(witness(), critical_fields=(CriticalParseField(("title",), []),)),
    replace(witness(), critical_fields=(CriticalParseField(("title",), "string", required=1),)),
    replace(witness(), critical_fields=(CriticalParseField(("title",), "string",
                             expected_sha256="hash"),)),
])
def test_untrusted_witness_is_rejected_before_comparison(bad):
    with pytest.raises(ValueError, match="invalid_parser_witness_review"):
        review(baseline=bad)


@pytest.mark.parametrize("change", [
    {"scope": "not-hex"}, {"input_hash": "invalid"},
    {"version": "bad version"}, {"complete": 1}, {"raw": []},
])
def test_malformed_new_parse_is_never_classified_as_a_regression(change):
    with pytest.raises(ValueError, match="invalid_parser_witness_review"):
        review(**change)


@pytest.mark.parametrize("value", [float("nan"), object(), "x" * 70000])
def test_invalid_or_unbounded_stable_value_is_not_hashed(value):
    with pytest.raises(ValueError, match="invalid_parser_witness_field"):
        field_digest(value)
