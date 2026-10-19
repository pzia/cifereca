"""P0 #979: ADEPT return command arguments and vendor errors are a privacy boundary.

All scenarios are synthetic: no real Adobe identity, loan, file, CLI or network.
"""
from __future__ import annotations

import logging
import traceback
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import AttentionRequired
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime


SECRET = "ADOBE_PRIVATE_ACCESS_TOKEN_IN_EXCEPTION"


def runtime():
    return KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=logging.getLogger("test.safe.adept"),
        report_diagnostic=lambda *args, **kwargs: None,
    ))


class HostileReference:
    def __str__(self):
        raise AssertionError("untrusted references must never be coerced to argv strings")


@pytest.mark.parametrize("bad", [
    None, False, True, 123, {}, [], HostileReference(),
    "", "  ", "loan ", " loan", "-loan", "\tloan", "loan\n" + SECRET,
    "loan\x7f" + SECRET, "loan\u200b" + SECRET, "loan\ud800",
    "x" * 257,
])
@pytest.mark.parametrize("field", ["adept_loan_id", "adept_profile_name"])
def test_return_binding_rejects_unqualified_argv_before_any_cli(bad, field):
    r = runtime()
    metadata = {"adept_loan_id": "loan-10", "adept_profile_name": "Original profile"}
    metadata[field] = bad
    with pytest.raises(AttentionRequired) as caught:
        r.execute({"operation": "return", "metadata": metadata})
    assert caught.value.code == "adept_return_binding_required"
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("metadata", [
    None, "", [], ["loan-10"], "not a dict", 15, {"adept_loan_id": "loan-10"},
])
def test_missing_or_hostile_metadata_shape_fails_with_fixed_error(metadata):
    with pytest.raises(AttentionRequired) as caught:
        runtime().execute({"operation": "return", "metadata": metadata})
    assert caught.value.code == "adept_return_binding_required"


def test_valid_opaque_loan_and_profile_names_preserved_exactly():
    metadata = {
        "adept_loan_id": "urn:uuid:550e8400-e29b-41d4-a716-446655440000",
        "adept_profile_name": "Profil Adobe d'origine",
    }
    assert runtime()._return_binding({"metadata": metadata}) == (
        metadata["adept_loan_id"], metadata["adept_profile_name"],
    )


def test_vendor_inventory_exception_is_not_chained_into_public_traceback():
    r = runtime()
    r._available = lambda *args: True
    r.run_json = lambda *args, **kwargs: {
        "ok": True, "operation": "loan_return",
        "loan_id": "loan-10", "profile": "Original profile",
    }

    def private_inventory(_name):
        raise RuntimeError("raw vendor error " + SECRET
                           + " <fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>")

    r.loans = private_inventory
    with pytest.raises(KoboAdeptError) as caught:
        r.return_loan("loan-10", "Original profile")

    error = caught.value
    assert error.code == "adept_return_verification_unavailable"
    assert error.retryable is False
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    public_traceback = "".join(traceback.format_exception(error))
    assert SECRET not in public_traceback
    assert "PRIVATE_LICENSE" not in public_traceback
    assert r.return_diagnostic()["phase"] == "failed"
    assert r.return_diagnostic()["cli_confirmed"] is True
