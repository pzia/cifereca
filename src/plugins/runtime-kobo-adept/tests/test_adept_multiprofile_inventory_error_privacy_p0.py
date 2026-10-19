"""P0 #979: per-profile inventory errors must never serialize vendor secrets.

Synthetic adapter seam only; no Adobe profile, license, account, CLI, network,
physical device, key, loan mutation or real private inventory is accessed.
"""
from types import SimpleNamespace
import pytest

from cifereca.plugin_api import AttentionRequired
from cifereca_kobo_adept_runtime import (
    KoboAdeptRuntime, _INVENTORY_PUBLIC_ERRORS, _INVENTORY_FALLBACK_ERROR,
    _INVENTORY_FALLBACK_MESSAGE,
)

PRIVATE = "PRIVATE_LICENSE_TOKEN_987654"
SENSITIVE = (
    "https://fulfillment.invalid/loan?token=" + PRIVATE,
    "<fulfillmentToken>" + PRIVATE + "</fulfillmentToken>",
    "alice@example.invalid",
    "/home/private/.adobe-digital-editions/activation.xml",
)


def runtime():
    r = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]},
        logger=None, report_diagnostic=lambda *a, **k: None,
    ))
    r.contract_version = 8
    return r


class ExplosiveAttention(AttentionRequired):
    def __str__(self):
        raise AssertionError("Core projection must not stringify vendor exceptions")


@pytest.mark.parametrize("known", sorted(_INVENTORY_PUBLIC_ERRORS))
def test_known_inventory_failure_is_fixed_and_not_vendor_text(known):
    r = runtime()
    def get_profile(profile, *, expired=False):
        if profile == "valid":
            return {"loans": [{"id": "loan-1", "expired": False,
                               "returned": False, "token_present": True}]}
        raise AttentionRequired(" ".join(SENSITIVE), code=known)
    r._loan_inventory = get_profile

    result = r.loan_inventories(["valid", "broken"])
    assert result["complete"] is False
    assert len(result["profiles"]) == 1
    assert result["profiles"][0]["profile"] == "valid"
    assert result["errors"] == [{
        "profile": "broken", "code": known,
        "message": _INVENTORY_PUBLIC_ERRORS[known],
    }]
    assert all(s not in repr(result) for s in SENSITIVE)


@pytest.mark.parametrize("code", [
    "vendor_code_" + PRIVATE, "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
    "https://fulfillment.invalid?token=" + PRIVATE,
    None, True, False, 42, ["token"], {"secret": PRIVATE},
])
def test_unknown_or_untyped_error_code_never_passes_to_core(code):
    r = runtime()
    def get_profile(profile, *, expired=False):
        raise AttentionRequired(" ".join(SENSITIVE), code=code)
    r._loan_inventory = get_profile
    result = r.loan_inventories(["broken"])
    assert result == {
        "profiles": [],
        "errors": [{
            "profile": "broken",
            "code": _INVENTORY_FALLBACK_ERROR,
            "message": _INVENTORY_FALLBACK_MESSAGE,
        }],
        "complete": False,
    }
    assert PRIVATE not in repr(result)


def test_no_error_str_or_vendor_traceback_was_consulted():
    r = runtime()
    def get_profile(profile, *, expired=False):
        raise ExplosiveAttention(" ".join(SENSITIVE), code="kobo_adept_failed")
    r._loan_inventory = get_profile
    result = r.loan_inventories(["broken"])
    assert result["errors"] == [{
        "profile": "broken", "code": "kobo_adept_failed",
        "message": _INVENTORY_PUBLIC_ERRORS["kobo_adept_failed"],
    }]


def test_valid_empty_or_healthy_inventories_remain_complete():
    r = runtime()
    r._loan_inventory = lambda profile, *, expired=False: {"loans": []}
    result = r.loan_inventories(["valid"])
    assert result == {
        "profiles": [{"profile": "valid", "loans": []}],
        "errors": [],
        "complete": True,
    }
