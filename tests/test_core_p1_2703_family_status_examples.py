"""#2703: machine-checkable examples for the four status levels of all 7 families.

Synthetic observations ONLY. These rows are not field qualifications or
mutating permissions for actual Cifereca suppliers. No provider I/O.
"""
from cifereca.plugin_api import (
    CAPABILITY_FAMILIES, CapabilityEvidence, CapabilityMatrix,
    exact_capability_families,
)


OPERATIONS = {
    "catalogue": ("catalog.search", "catalog.detail", "catalog.refresh", "catalog.filters"),
    "metadata": ("metadata.enrichment", "metadata.title", "metadata.isbn", "metadata.cover"),
    "accounts_rights": ("account.login", "rights.check", "personal_inventory.list", "sync.entitlements"),
    "loans": ("loan.inventory", "loan.borrow", "booking.create", "sync.remote_inventory"),
    "acquisition": ("acquisition.download", "acquisition.offer", "acquisition.purchase", "sync.local_materialization"),
    "drm": ("drm.adept", "drm.lcp", "transform.acsm", "drm.license"),
    "crosscutting": ("cache.refresh", "artifact.backup", "diagnostics.report", "sync.scheduled_refresh"),
}
STATUSES = ("supported", "partial", "unsupported", "unverified")


def _evidence(operation, status, *, edition="epub-1", account="reader-a"):
    return CapabilityEvidence(
        provider_id="synthetic", operation=operation, scope="edition",
        identity_id="alice", account_id=account, credential_id="card-a",
        edition_id=edition, status=status,
        implementation_state="implemented",
        source_kind="account_observation" if status == "supported" else "code",
        evidence_ref="synthetic:explicit-scope" if status == "supported" else "",
        reason="synthetic_partial_coverage" if status == "partial" else "",
    )


def _group(matrix, *, edition="epub-1", account="reader-a"):
    return {x.family: x for x in exact_capability_families(
        matrix, scope="edition", identity_id="alice",
        account_id=account, credential_id="card-a", edition_id=edition,
    )}


def test_every_family_preserves_four_distinct_states_without_global_permission():
    assert tuple(OPERATIONS) == CAPABILITY_FAMILIES
    rows = tuple(
        _evidence(operation, status)
        for operations in OPERATIONS.values()
        for operation, status in zip(operations, STATUSES)
    )
    grouped = _group(CapabilityMatrix("synthetic", rows))
    assert len(rows) == 28
    for family, operations in OPERATIONS.items():
        bucket = grouped[family]
        assert bucket.missing is False
        assert {row.operation for row in bucket.evidence} == set(operations)
        assert (bucket.supported_count, bucket.partial_count,
                bucket.unsupported_count, bucket.unverified_count) == (1, 1, 1, 1)
        assert bucket.authorizes_mutation is False
        assert all(row.authorizes_mutation is False for row in bucket.evidence)


def test_account_and_edition_are_not_widened_by_other_positive_evidence():
    rows = (
        _evidence("loan.borrow", "supported", edition="epub-a", account="reader-a"),
        _evidence("loan.borrow", "unsupported", edition="pdf-b", account="reader-a"),
        _evidence("loan.borrow", "unverified", edition="epub-a", account="reader-b"),
    )
    matrix = CapabilityMatrix("synthetic", rows)
    a = _group(matrix, edition="epub-a", account="reader-a")
    b = _group(matrix, edition="pdf-b", account="reader-a")
    c = _group(matrix, edition="epub-a", account="reader-b")
    assert a["loans"].supported_count == 1
    assert b["loans"].unsupported_count == 1
    assert c["loans"].unverified_count == 1
    assert all(a[family].missing for family in CAPABILITY_FAMILIES if family != "loans")
    assert _group(matrix, edition="pdf-b", account="reader-b")["loans"].missing
    assert all(not item.authorizes_mutation for results in (a, b, c)
               for item in results.values())
