"""P0 #5055: a provider-neutral Core SQLite display link grants no entitlement."""
from dataclasses import replace

import pytest

from cifereca.core.observed_loan_scope import observed_loan
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_bnf_import_library_link import bnf_importer, staged


def _receipt(bnf_importer):
    service, loan, requests = bnf_importer
    result = service.import_acsm(
        staged(service, loan), identity_id=loan.identity_id,
        placement_provider_id="bnf-pret",
    )
    assert result["status"] == "complete"
    return service, loan, result


def _bind(storage, result, scope, *, placement="bnf-pret",
          field="fixture_observed_loan"):
    return storage.acquisition_bind_observed_display_loan(
        result["acquisition_id"], path=result["path"],
        artifact_id=result["artifact_id"], scope=scope,
        placement_provider_id=placement, provenance_field=field,
    )


def test_generic_cas_uses_declared_metadata_key_and_same_indexed_placement(bnf_importer):
    service, loan, result = _receipt(bnf_importer)
    scope = observed_loan(loan)
    before_entitlements = service.storage.entitlements()
    assert _bind(service.storage, result, scope)
    record = service.storage.acquisition_get(result["acquisition_id"])
    assert record["metadata"]["fixture_observed_loan"] == scope
    assert "bnf_observed_loan" not in record["metadata"]
    assert service.storage.entitlements() == before_entitlements
    assert _bind(service.storage, result, scope)  # exact idempotent CAS
    assert not _bind(service.storage, result, dict(scope, account_id="other"))
    record = service.storage.acquisition_get(result["acquisition_id"])
    assert record["metadata"]["fixture_observed_loan"] == scope


@pytest.mark.parametrize(("field", "placement"), [
    ("other_observed_loan", "future-provider"),
    ("not-a-display-tag", "bnf-pret"),
    ("bnf_observed_loan", "unknown/provider"),
    ("", "bnf-pret"),
])
def test_untrusted_provider_metadata_or_placement_never_links(
    bnf_importer, field, placement,
):
    service, loan, result = _receipt(bnf_importer)
    before = service.storage.acquisition_get(result["acquisition_id"])["metadata"]
    assert not _bind(service.storage, result, observed_loan(loan),
                     placement=placement, field=field)
    after = service.storage.acquisition_get(result["acquisition_id"])["metadata"]
    assert before == after


@pytest.mark.parametrize("wrong", ["identity", "artifact", "path"])
def test_wrong_receipt_coordinates_never_write(bnf_importer, wrong):
    service, loan, result = _receipt(bnf_importer)
    scope = observed_loan(loan)
    arguments = dict(result)
    if wrong == "identity":
        scope["identity_id"] = "someone-else"
    elif wrong == "artifact":
        arguments["artifact_id"] = "sha256:" + "0" * 64
    else:
        arguments["path"] = "/outside/private.epub"
    assert not _bind(service.storage, arguments, scope)
    assert "fixture_observed_loan" not in service.storage.acquisition_get(
        result["acquisition_id"]
    )["metadata"]


def test_existing_bnf_api_is_still_cas_compatible(bnf_importer):
    service, loan, result = _receipt(bnf_importer)
    first = observed_loan(loan)
    next_borrower = observed_loan(replace(loan, source_account_id="other"))
    assert service.storage.acquisition_bind_bnf_display_loan(
        result["acquisition_id"], path=result["path"],
        artifact_id=result["artifact_id"], scope=first,
    )
    assert not service.storage.acquisition_bind_bnf_display_loan(
        result["acquisition_id"], path=result["path"],
        artifact_id=result["artifact_id"], scope=next_borrower,
    )
    assert service.storage.entitlements() == []
