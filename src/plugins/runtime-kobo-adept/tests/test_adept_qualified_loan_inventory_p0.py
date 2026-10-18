"""P0 #979: opaque loan-registry JSON must not leak devices or vendor payloads.

This file exercises a synthetic run_json seam: no Adobe account, ACSM, file
access, network, subprocess or real private loan data.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime

SECRETS = (
    "PRIVATE_TOKEN", "PRIVATE_CERT", "alice@example.invalid",
    "https://vendor.invalid/fulfill?token=PRIVATE_TOKEN",
    "<fulfillmentToken>PRIVATE_CERT</fulfillmentToken>",
)


def record(number="loan-1", **overrides):
    return {
        "id": number, "expired": False, "returned": False,
        "token_present": True, "title": "Livre public",
        "validity": "2026-12-31", "local_book": "/tmp/real-reader-book.epub",
        "devices": [{"token": "PRIVATE_TOKEN", "cert": "PRIVATE_CERT",
                     "user": "alice@example.invalid", "nested": {
                         "license": "<fulfillmentToken>PRIVATE_CERT</fulfillmentToken>",
                     }}],
        "fetched_at": "PRIVATE_TOKEN", "returned_at": "PRIVATE_TOKEN",
        "operator_url": SECRETS[3], "token_path": SECRETS[4],
        **overrides,
    }


def inventory(rows, *, expired=False):
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *args, **kwargs: None,
    ))
    runtime.contract_version = 8
    calls = []
    def run_json(args, **kwargs):
        calls.append((list(args), kwargs))
        return {"ok": True, "loans": deepcopy(rows),
                "credentials": {"private": "PRIVATE_TOKEN"}}
    runtime.run_json = run_json
    result = runtime.expired_loans("profile-A") if expired else runtime.loans("profile-A")
    assert calls == [(
        ["loans", "expired" if expired else "list", "--profile", "profile-A", "--json"],
        {"timeout": 30, "max_output_bytes": 4 * 1024 * 1024},
    )]
    return result, runtime


def test_private_nested_device_objects_and_incidental_fields_are_dropped():
    result, runtime = inventory([record()])
    assert result == {"loans": [{
        "id": "loan-1", "expired": False, "returned": False,
        "token_present": True, "title": "Livre public",
        "validity": "2026-12-31", "local_book": "/tmp/real-reader-book.epub",
    }]}
    assert runtime.last_run_diagnostic == {"loan_count": 1, "inventory_valid": True}
    public = repr(result) + repr(runtime.last_run_diagnostic)
    for secret in SECRETS:
        assert secret not in public


def test_multiple_profiles_keep_exact_namespace_no_cross_profile_deduplication():
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={}, logger=None, report_diagnostic=lambda *a, **k: None,
    ))
    runtime.contract_version = 8
    runtime.run_json = lambda args, **kwargs: {
        "ok": True, "loans": [record("repeated-in-different-profile")],
    }
    result = runtime.loan_inventories(["profile-A", "profile-B"])
    assert result["complete"] is True and not result["errors"]
    assert [p["profile"] for p in result["profiles"]] == ["profile-A", "profile-B"]
    assert all(p["loans"][0]["id"] == "repeated-in-different-profile"
               for p in result["profiles"])
    assert all("devices" not in p["loans"][0] for p in result["profiles"])


@pytest.mark.parametrize("bad", [
    "", " loan-1", "loan-1 ", "-loan", "loan\nPRIVATE_TOKEN",
    "loan\rPRIVATE_TOKEN", "loan\x7fPRIVATE_TOKEN", "x" * 257,
    None, True, 42, {}, ["loan"], "PRIVATE_TOKEN\x00",
])
def test_invalid_loan_identifiers_fail_closed_with_static_error(bad):
    with pytest.raises(KoboAdeptError, match="Inventaire|Entrée") as exc:
        inventory([record(bad)])
    assert exc.value.code == "kobo_adept_invalid_loan_inventory"
    assert "PRIVATE_TOKEN" not in str(exc.value)


def test_duplicated_ids_within_one_profile_are_not_ambiguous_choices():
    with pytest.raises(KoboAdeptError, match="Entrée de prêt ADEPT invalide"):
        inventory([record("loan-1"), record("loan-1", returned=True)])
    # No caller or Core may infer the right returned state from duplicate IDs.


@pytest.mark.parametrize("field,value", [
    ("expired", "false"), ("returned", 1), ("token_present", None),
    ("token_present", "PRIVATE_TOKEN"),
])
def test_state_boolean_fields_are_strictly_typed(field, value):
    with pytest.raises(KoboAdeptError, match="Entrée de prêt ADEPT invalide"):
        inventory([record(**{field: value})])


def test_expired_feed_rejects_nonexpired_and_returned_rows():
    with pytest.raises(KoboAdeptError, match="Inventaire expiré incohérent"):
        inventory([record(expired=False)], expired=True)
    with pytest.raises(KoboAdeptError, match="Inventaire expiré incohérent"):
        inventory([record(expired=True, returned=True)], expired=True)
    result, _ = inventory([record(expired=True, returned=False)], expired=True)
    assert result["loans"][0]["expired"] is True


@pytest.mark.parametrize(("field", "bad_value"), [
    ("title", "PRIVATE_TOKEN\n"), ("title", {"value": "PRIVATE_TOKEN"}),
    ("title", "x" * 513), ("title", ""),
    ("validity", "PRIVATE_TOKEN"), ("validity", "2026-99-99"),
    ("validity", "2026-02-30"), ("validity", 123),
    ("local_book", SECRETS[3]), ("local_book", "relative/token.epub"),
    ("local_book", "/tmp/PRIVATE_TOKEN\nfile.epub"),
    ("local_book", {"url": SECRETS[3]}),
])
def test_optional_unqualified_fields_are_omitted_without_rejecting_loans(field, bad_value):
    result, runtime = inventory([record(**{field: bad_value})])
    row = result["loans"][0]
    assert row["id"] == "loan-1"
    assert field not in row
    assert "PRIVATE_TOKEN" not in repr(result)
    assert runtime.last_run_diagnostic["inventory_valid"] is True


def test_valid_loan_binding_fields_still_preserve_real_path_as_string():
    path = "/home/user/.config/adept/books/book.epub"
    result, _ = inventory([record(local_book=path)])
    assert result["loans"][0]["local_book"] == path
    assert Path(result["loans"][0]["local_book"]).is_absolute()


def test_multiple_independent_valid_loan_ids_are_not_collapsed():
    result, _ = inventory([record("urn:uuid:aaa"), record("urn:uuid:bbb", returned=True)])
    assert len(result["loans"]) == 2
    assert result["loans"][0]["id"] == "urn:uuid:aaa"
    assert result["loans"][1]["returned"] is True
