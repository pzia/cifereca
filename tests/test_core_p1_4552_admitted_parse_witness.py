"""#4552: exact source identity+edition prevents cross-profile witness reuse."""
from dataclasses import replace

import pytest

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.core.notice_parse_witness import (
    ParseWitness, CriticalParseField,
    admitted_source_scope_sha256, review_admitted_source_parse_witness,
)

KEY = NoticeSourceKey(
    provider_id="paris", visibility="private",
    identity_id="alice", credential_id="card-A",
    account_id="borrower-A", branch_id="DILICOM",
    record_id="record:01", edition_id="edition:epub",
    manifestation_role="digital",
)
BODY_SHA = "b" * 64


def observation(key=KEY, *, version="2.0.0", fields=None, status="complete",
                parser_id="paris/notice"):
    return admit_source_notice(
        key=key, parser_id=parser_id, parser_version=version,
        observed_at=1700, status=status,
        raw_fields={"Title": "Un titre", "Edition": {"Digital": True}}
        if fields is None else fields,
    )


def witness(key=KEY, *, parser_id="paris/notice"):
    return ParseWitness(
        source_scope_sha256=admitted_source_scope_sha256(
            key, parser_id=parser_id,
        ),
        approved_input_sha256=BODY_SHA, parser_version="1.0.0",
        approved=True,
        critical_fields=(
            CriticalParseField(("Title",), "string"),
            CriticalParseField(("Edition", "Digital"), "boolean"),
        ),
    )


def test_exact_admitted_source_same_bytes_new_parser_loss_is_confirmed():
    current = observation(fields={"Title": "Un titre"})
    v = review_admitted_source_parse_witness(
        witness(), observation=current, input_sha256=BODY_SHA,
    )
    assert v.status == "parser_regression_confirmed"
    assert v.affected_fields == ("critical_field_2",)
    assert v.executable is False


@pytest.mark.parametrize("change", [
    {"identity_id": "bob"},
    {"credential_id": "card-B"},
    {"account_id": "borrower-B"},
    {"branch_id": "SYRACUSE"},
    {"record_id": "record:02"},
    {"edition_id": "edition:paper"},
    {"manifestation_role": "print"},
    {"provider_id": "bnf"},
])
def test_private_edition_profile_account_or_branch_change_does_not_reuse_witness(change):
    v = review_admitted_source_parse_witness(
        witness(),
        observation=observation(key=replace(KEY, **change),
                                fields={"Title": "Un titre"}),
        input_sha256=BODY_SHA,
    )
    assert v.status == "unverified"


def test_public_scope_has_no_private_credential_but_remains_exact():
    public = replace(
        KEY, provider_id="bnf", visibility="public",
        identity_id=None, credential_id=None, account_id=None,
    )
    match = review_admitted_source_parse_witness(
        witness(public), observation=observation(
            key=public, version="3.0.0", fields={"Title": "Un titre"},
        ), input_sha256=BODY_SHA,
    )
    assert match.status == "parser_regression_confirmed"


def test_incomplete_edition_and_changed_source_are_not_confirmed():
    b = witness()
    partial = review_admitted_source_parse_witness(
        b, observation=observation(status="partial", fields={}),
        input_sha256=BODY_SHA,
    )
    changed = review_admitted_source_parse_witness(
        b, observation=observation(fields={}),
        input_sha256="c" * 64,
    )
    assert partial.status == "unverified"
    assert changed.status == "source_changed"


def test_parser_implementation_is_part_of_source_scope_but_version_is_not():
    one = admitted_source_scope_sha256(KEY, parser_id="paris/notice")
    two = admitted_source_scope_sha256(KEY, parser_id="other/notice")
    assert one != two
    assert one == witness().source_scope_sha256
    assert one == admitted_source_scope_sha256(KEY, parser_id="paris/notice")
    result = review_admitted_source_parse_witness(
        witness(), observation=observation(parser_id="other/notice", fields={}),
        input_sha256=BODY_SHA,
    )
    assert result.status == "unverified"


def test_forged_json_or_revision_is_not_accepted_as_admitted_raw_notice():
    v = observation()
    for tamper in (
        replace(v, raw_json='{"Title":"other"}'),
        replace(v, source_revision="notice-raw.v1:" + "0" * 64),
        replace(v, raw_json='{"refresh_token":"do-not-use"}'),
    ):
        with pytest.raises(ValueError, match="invalid_admitted_source_observation"):
            review_admitted_source_parse_witness(
                witness(), observation=tamper, input_sha256=BODY_SHA,
            )


def test_invalid_raw_observation_type_and_input_digest_refused():
    with pytest.raises(ValueError, match="invalid_admitted_source_observation"):
        review_admitted_source_parse_witness(
            witness(), observation=object(), input_sha256=BODY_SHA,
        )
    with pytest.raises(ValueError, match="invalid_parser_witness_review"):
        review_admitted_source_parse_witness(
            witness(), observation=observation(), input_sha256="bad",
        )


def test_revision_is_opaque_no_user_identifier_exposed_by_diagnostic():
    report = review_admitted_source_parse_witness(
        witness(), observation=observation(fields={}),
        input_sha256=BODY_SHA,
    )
    for private in ("alice", "card-A", "borrower-A", "record:01"):
        assert private not in repr(report)
    assert report.affected_fields == ("critical_field_1", "critical_field_2")
