"""#4552: approved parser witnesses persist without raw HTTP or cross-profile reuse."""
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_parse_witness import (
    CriticalParseField, admitted_source_scope_sha256,
    review_admitted_source_parse_witness,
)
from cifereca.core.notice_parse_witness_store import (
    _PREFIX, publish_approved_parse_witness, read_approved_parse_witness,
)
from tests.test_core_p1_4552_admitted_parse_witness import KEY, observation


RESPONSE = b"<record>original authorized cached input</record>"
FIELDS = (
    CriticalParseField(("Title",), "string"),
    CriticalParseField(("Edition", "Digital"), "boolean"),
)


def put(db, *, version="1.0.0", body=RESPONSE, fields=FIELDS,
        expected_generation=0, observed=None):
    return publish_approved_parse_witness(
        db, observation=observation(version=version) if observed is None else observed,
        response_bytes=body, critical_fields=fields,
        expected_generation=expected_generation,
    )


def test_explicit_baseline_survives_restart_no_response_body_or_private_names(tmp_path):
    path = tmp_path / "witness.sqlite"
    db = Storage(path)
    recorded = put(db)
    assert recorded.generation == 1 and recorded.baseline.approved is True
    assert put(db, expected_generation=1) == recorded  # no generation churn
    assert read_approved_parse_witness(
        db, key=KEY, parser_id="paris/notice",
    ) == recorded
    scope = admitted_source_scope_sha256(KEY, parser_id="paris/notice")
    raw = db.metadata_get(_PREFIX + scope)
    assert raw is not None
    for sensitive in ("alice", "card-A", "borrower-A", "record:01",
                      RESPONSE.decode(), "Un titre"):
        assert sensitive not in raw
    decoded = json.loads(raw)
    assert decoded["generation"] == 1
    assert decoded["baseline"]["approved_input_sha256"] == sha256(RESPONSE).hexdigest()
    db.close(final=True)

    reopened = Storage(path)
    assert read_approved_parse_witness(
        reopened, key=KEY, parser_id="paris/notice",
    ) == recorded
    assert reopened.entitlements() == []
    assert reopened.acquisitions() == []
    reopened.close(final=True)


def test_exact_private_source_scope_and_version_change_classify_after_restart(tmp_path):
    db = Storage(tmp_path / "exact.sqlite")
    approved = put(db)
    other = replace(KEY, identity_id="bob", credential_id="card-B",
                    account_id="borrower-B")
    assert read_approved_parse_witness(db, key=other, parser_id="paris/notice") is None
    assert read_approved_parse_witness(db, key=KEY, parser_id="other/parser") is None
    current = observation(version="2.0.0", fields={"Title": "Un titre"})
    report = review_admitted_source_parse_witness(
        approved.baseline, observation=current,
        input_sha256=sha256(RESPONSE).hexdigest(),
    )
    assert report.status == "parser_regression_confirmed"
    assert report.affected_fields == ("critical_field_2",)
    changed = review_admitted_source_parse_witness(
        approved.baseline, observation=current, input_sha256=sha256(b"new").hexdigest(),
    )
    assert changed.status == "source_changed"
    db.close(final=True)


def test_cas_blocks_late_approval_and_requires_explicit_new_generation(tmp_path):
    db = Storage(tmp_path / "cas.sqlite")
    first = put(db)
    with pytest.raises(ValueError, match="parse_witness_generation_changed"):
        put(db, body=b"new source", expected_generation=0)
    second = put(db, body=b"new source", expected_generation=first.generation)
    assert second.generation == 2 and second.baseline != first.baseline
    assert read_approved_parse_witness(
        db, key=KEY, parser_id="paris/notice",
    ) == second
    db.close(final=True)


@pytest.mark.parametrize("observation_override,fields", [
    (observation(status="partial"), FIELDS),
    (observation(fields={"Title": "Un titre"}), FIELDS),
    (observation(), (CriticalParseField(("Missing",), "string"),)),
    (replace(observation(), source_revision="forged"), FIELDS),
])
def test_incomplete_or_forged_baseline_cannot_be_approved(
    tmp_path, observation_override, fields,
):
    db = Storage(tmp_path / "invalid.sqlite")
    with pytest.raises(ValueError, match="invalid_parse_witness_publication"):
        put(db, observed=observation_override, fields=fields)
    assert read_approved_parse_witness(db, key=KEY, parser_id="paris/notice") is None
    db.close(final=True)


def test_corrupt_durable_baseline_fails_closed_without_repair_or_overwrite(tmp_path):
    db = Storage(tmp_path / "corrupt.sqlite")
    scope = admitted_source_scope_sha256(KEY, parser_id="paris/notice")
    db.metadata_set(_PREFIX + scope, '{"schema":1,"schema":1}')
    with pytest.raises(ValueError, match="corrupt_parse_witness_baseline"):
        read_approved_parse_witness(db, key=KEY, parser_id="paris/notice")
    with pytest.raises(ValueError, match="corrupt_parse_witness_baseline"):
        put(db)
    assert db.metadata_get(_PREFIX + scope) == '{"schema":1,"schema":1}'
    db.close(final=True)


@pytest.mark.parametrize("invalid", [b"", b"x" * (2 * 1024 * 1024 + 1), "text", None])
def test_raw_input_budget_does_not_store_any_baseline(tmp_path, invalid):
    db = Storage(tmp_path / "budget.sqlite")
    with pytest.raises(ValueError, match="invalid_parse_witness_publication"):
        put(db, body=invalid)
    assert read_approved_parse_witness(db, key=KEY, parser_id="paris/notice") is None
    db.close(final=True)
