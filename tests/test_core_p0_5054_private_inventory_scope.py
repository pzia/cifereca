"""P0 #5054: pure private batch shape guard never attests ownership itself."""
from dataclasses import replace
import hashlib

import pytest

from cifereca.models import SearchResult
from cifereca.core.private_inventory_row_scope import (
    MAX_PRIVATE_ROWS, private_inventory_rows_match_scope,
)
from cifereca.core.private_inventory_row_scope import credential_scoped_private_inventory_rows_valid


def _row(provider="fixture-store", credential="cred", collection="library",
         scenario="FIXTURE_PRIVATE", record="scoped:edition"):
    return SearchResult(
        id=record, record_id=record, docbase="FIXTURE", medium="digital",
        title="Fictional book", author=None, type=None, format=None,
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario=scenario,
        source_provider=provider, source_credential=credential,
        source_collection=collection, owned=True,
    )


def _valid(rows, **kw):
    args = dict(provider_id="fixture-store", credential_id="cred",
                collection="library", scenario="FIXTURE_PRIVATE",
                scoped_record_valid=lambda rid: rid.startswith("scoped:"))
    args.update(kw)
    return private_inventory_rows_match_scope(rows, **args)


def test_unknown_private_provider_shape_checked_without_provider_allowlist():
    assert _valid([_row()])
    assert not _valid([_row(), _row(provider="foreign")])
    assert not _valid([_row(), _row(credential="other")])
    assert not _valid([_row(), _row(collection="catalogue")])
    assert not _valid([_row(), _row(scenario="PUBLIC")])
    assert not _valid([_row(), replace(_row(), owned=1)])
    assert not _valid([_row(), replace(_row(), id="other")])
    assert not _valid([_row(), replace(_row(), record_id="unscoped")])


def test_invalid_source_or_record_validator_fails_closed():
    assert not _valid([_row()], credential_id="")
    assert not _valid([_row()], provider_id=None)
    assert not _valid([_row()], scoped_record_valid=lambda _record: 1)
    assert not _valid([_row()], scoped_record_valid=lambda _record: (_ for _ in ()).throw(ValueError("private")))
    assert not _valid((_row(),))
    assert not _valid([_row()] * (MAX_PRIVATE_ROWS + 1))


def test_numilog_compatibility_requires_exact_hashed_credential_lid_and_variant():
    credential = "numilog-private"
    scoped = "numilog:" + hashlib.sha256(credential.encode()).hexdigest()[:20]
    record = scoped + ":lid-1:" + "a" * 16
    result = _row(
        provider="numilog", credential=credential,
        scenario="NUMILOG_LIBRARY", record=record,
    )
    assert credential_scoped_private_inventory_rows_valid([result], provider_id="numilog", credential_id=credential, collection="library", scenario="NUMILOG_LIBRARY")
    assert not credential_scoped_private_inventory_rows_valid([replace(result, source_collection="catalogue")], provider_id="numilog", credential_id=credential, collection="library", scenario="NUMILOG_LIBRARY")
    assert not credential_scoped_private_inventory_rows_valid([replace(result, owned=False)], provider_id="numilog", credential_id=credential, collection="library", scenario="NUMILOG_LIBRARY")
    assert not credential_scoped_private_inventory_rows_valid([replace(result, source_credential="foreign")], provider_id="numilog", credential_id=credential, collection="library", scenario="NUMILOG_LIBRARY")
    assert not credential_scoped_private_inventory_rows_valid([replace(result, record_id="numilog:guess")], provider_id="numilog", credential_id=credential, collection="library", scenario="NUMILOG_LIBRARY")
    assert not credential_scoped_private_inventory_rows_valid([replace(result, source_provider="fixture-store")], provider_id="numilog", credential_id=credential, collection="library", scenario="NUMILOG_LIBRARY")
    assert not credential_scoped_private_inventory_rows_valid([result], provider_id="numilog", credential_id="foreign", collection="library", scenario="NUMILOG_LIBRARY")


# A selected-purchase session opts in via a static provider-owned record shape.
# The Core checks structure only; no inventory row can grant a future GET.
def test_declared_private_scope_for_future_provider_is_neutral():
    from types import SimpleNamespace
    from cifereca.core.private_inventory_row_scope import declared_private_inventory_batch_valid
    import hashlib
    credential = "future-credential"
    digest = hashlib.sha256(credential.encode()).hexdigest()[:20]
    record = f"future-store:{digest}:lid-a:{'a' * 16}"
    rows = [_row(provider="future-store", credential=credential,
                 scenario="PRIVATE_LEDGER", record=record)]
    session = SimpleNamespace(
        selected_purchase_branch="private-library",
        private_inventory_row_contract=(
            "library", "PRIVATE_LEDGER", "credential_scoped_record_v1",
        ),
    )
    assert declared_private_inventory_batch_valid(
        rows, session=session, provider_id="future-store",
        credential_id=credential,
    )
    assert not declared_private_inventory_batch_valid(
        [rows[0], replace(rows[0], source_credential="other")],
        session=session, provider_id="future-store", credential_id=credential,
    )
    assert not declared_private_inventory_batch_valid(
        [replace(rows[0], record_id="unverified")],
        session=session, provider_id="future-store", credential_id=credential,
    )


@pytest.mark.parametrize("contract", [
    None, "not-a-tuple", ("library", "PRIVATE_LEDGER"),
    ("library", "PRIVATE_LEDGER", "permissive"),
    ("library", "PRIVATE_LEDGER", "credential_scoped_record_v1", "extra"),
])
def test_selected_purchase_without_valid_declared_scope_fails_closed(contract):
    from types import SimpleNamespace
    from cifereca.core.private_inventory_row_scope import declared_private_inventory_batch_valid
    session = SimpleNamespace(selected_purchase_branch="purchase")
    if contract is not None:
        session.private_inventory_row_contract = contract
    assert not declared_private_inventory_batch_valid(
        [], session=session, provider_id="future-store", credential_id="cred",
    )


def test_provider_without_selected_purchase_contract_unchanged():
    from types import SimpleNamespace
    from cifereca.core.private_inventory_row_scope import declared_private_inventory_batch_valid
    assert declared_private_inventory_batch_valid(
        [], session=SimpleNamespace(), provider_id="other-store",
        credential_id="cred",
    )
    # A declaration without a pinned branch may not bypass validation.
    assert not declared_private_inventory_batch_valid(
        [], session=SimpleNamespace(private_inventory_row_contract=(
            "library", "PRIVATE_LEDGER", "credential_scoped_record_v1",
        )), provider_id="other-store", credential_id="cred",
    )
