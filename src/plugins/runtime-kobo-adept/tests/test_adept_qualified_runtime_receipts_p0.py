"""P0 #979 / #1120: Core must never receive opaque vendor success JSON.

The runtime's return/download seams are simulated, with no subprocess,
account, Adobe identity, real license, device or filesystem mutations.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime

SECRET = (
    "ALICE_PRIVATE_SESSION",
    "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>",
    "https://fulfillment.invalid/certificate?token=PRIVATE_SESSION",
    "alice@example.invalid",
    "PRIVATE_LICENSE",
    "PRIVATE_SESSION",
)
FORGED = {
    "email": SECRET[3], "user": {"certificate": SECRET[1]},
    "license": SECRET[1], "fulfillment_url": SECRET[2],
    "extra": ["PRIVATE_LICENSE", {"authorization": "ALICE_PRIVATE_SESSION"}],
}


def runtime():
    return KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *args, **kwargs: None,
    ))


def returned(r, response):
    r.return_loan = MethodType(lambda self, loan, profile: deepcopy(response), r)
    return r.execute({"operation": "return", "metadata": {
        "adept_loan_id": "loan-17", "adept_profile_name": "original-profile",
    }})


def fulfilled(r, response):
    r.download_request = MethodType(lambda self, payload: deepcopy(response), r)
    return r.execute({
        "operation": "fulfill",
        "media_format": "epub",
        "source": {"type": "acsm"},
        "destination": "/tmp/synthetic-artifact.epub",
        "metadata": {"adept_profile_name": "original-profile"},
    })


def valid_return(**extra):
    return {
        "ok": True, "operation": "loan_return",
        "loan_id": "loan-17", "profile": "original-profile",
        **deepcopy(FORGED), **extra,
    }


def valid_fulfill(**extra):
    return {
        "ok": True, "loan_id": "loan-17", "profile": "original-profile",
        "artifact": {
            "path": "/tmp/synthetic-artifact.epub",
            "format": "EPUB", "sha256": "A"*64,
            "license_blob": SECRET[1], "token": "PRIVATE_LICENSE",
        },
        **deepcopy(FORGED), **extra,
    }


def assert_no_vendor_private_metadata(value):
    published = repr(dict(value.metadata))
    for secret in SECRET:
        assert secret not in published
    assert "fulfillment_url" not in published
    assert "license_blob" not in published
    assert "authorization" not in published
    assert "certificate" not in published


def test_return_proves_exact_loan_and_origin_but_discards_vendor_json():
    r = runtime()
    output = returned(r, valid_return())
    assert output.ok is True and output.operation == "return"
    assert output.artifact is None
    assert output.metadata == {
        "runtime_result": {
            "ok": True, "operation": "loan_return",
            "loan_id": "loan-17", "profile": "original-profile",
        },
        "adept_loan_id": "loan-17", "adept_profile_name": "original-profile",
        "loan_state": "returned", "artifact_preserved": True,
    }
    assert_no_vendor_private_metadata(output)


@pytest.mark.parametrize(("wrong_field", "replacement"), [
    ("ok", False), ("ok", "true"), ("operation", "download"),
    ("loan_id", "another-loan"), ("profile", "another-profile"),
])
def test_return_never_claims_success_for_a_contradictory_receipt(wrong_field, replacement):
    with pytest.raises(KoboAdeptError, match="incohérent") as exc:
        returned(runtime(), valid_return(**{wrong_field: replacement}))
    assert exc.value.code == "kobo_adept_invalid_response"
    assert "PRIVATE_LICENSE" not in str(exc.value)


def test_fulfillment_keeps_artifact_reference_but_only_bounded_receipt():
    output = fulfilled(runtime(), valid_fulfill())
    assert output.ok is True and output.operation == "fulfill"
    assert output.artifact.path == Path("/tmp/synthetic-artifact.epub")
    assert output.artifact.format == "epub"
    assert output.artifact.sha256 == "a"*64
    assert output.metadata == {
        "runtime_result": {
            "ok": True, "loan_id": "loan-17", "profile": "original-profile",
        },
        "adept_loan_id": "loan-17",
        "adept_profile_name": "original-profile",
    }
    assert_no_vendor_private_metadata(output)
    assert "PRIVATE_LICENSE" not in repr(output.artifact)


def test_fulfillment_without_optional_loan_does_not_invent_binding():
    response = valid_fulfill(loan_id=None, profile=None)
    result = fulfilled(runtime(), response)
    assert result.metadata["adept_loan_id"] is None
    assert result.metadata["adept_profile_name"] is None
    assert result.metadata["runtime_result"] == {
        "ok": True, "loan_id": None, "profile": None,
    }


@pytest.mark.parametrize(("key", "value"), [
    ("loan_id", ["loan-17"]), ("loan_id", "PRIVATE_LICENSE\n"),
    ("loan_id", "x"*257), ("loan_id", "-private"), ("loan_id", 1),
    ("profile", {"name": "original-profile"}), ("profile", "x"*257),
    ("profile", "PRIVATE_SESSION\r"), ("profile", "-other"), ("profile", False),
])
def test_untrusted_reference_type_or_bound_never_crosses_core(key, value):
    with pytest.raises(KoboAdeptError) as exc:
        fulfilled(runtime(), valid_fulfill(**{key: value}))
    assert exc.value.code == "kobo_adept_invalid_response"
    for secret in SECRET:
        assert secret not in str(exc.value)


@pytest.mark.parametrize(("artifact_field", "bad_value"), [
    ("format", "PRIVATE_SESSION"),
    ("format", "application/x-private-license"),
    ("format", "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>"),
    ("format", {"format": "epub"}),
    ("sha256", "PRIVATE_SESSION"),
    ("sha256", "f"*63),
    ("sha256", "g"*64),
    ("sha256", ["a"*64]),
])
def test_unqualified_artifact_descriptors_not_projected(artifact_field, bad_value):
    response = valid_fulfill()
    response["artifact"][artifact_field] = bad_value
    with pytest.raises(KoboAdeptError, match="non qualifiés") as exc:
        fulfilled(runtime(), response)
    assert exc.value.code == "kobo_adept_invalid_response"
    for secret in SECRET:
        assert secret not in str(exc.value)


@pytest.mark.parametrize("artifact", [None, [], {}, {"path": []}, {"path": 99}])
def test_missing_or_invalid_artifact_does_not_claim_mutation_success(artifact):
    with pytest.raises(KoboAdeptError) as exc:
        fulfilled(runtime(), valid_fulfill(artifact=artifact))
    assert exc.value.code == "kobo_adept_invalid_response"


def test_false_fulfillment_success_flag_does_not_claim_mutation_success():
    with pytest.raises(KoboAdeptError) as exc:
        fulfilled(runtime(), valid_fulfill(ok="true"))
    assert exc.value.code == "kobo_adept_invalid_response"
