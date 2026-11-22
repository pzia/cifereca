"""#4030: evidence-first order inside one bounded private cache page."""
from dataclasses import replace

from cifereca.core import local_origin_private_catalogue as private
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from tests.test_core_p0_4030_private_catalogue import private_rig, _run, rows
from tests.test_core_p0_4030_public_catalogue import rig, store  # transitive pytest fixtures
from tests.test_core_p0_3171_atomic_publication_v14 import new_source


def test_private_cached_loan_page_orders_strong_before_weak_evidence(private_rig):
    service, request, account, session, calls, path, first, snapshot = private_rig
    for name, author in [
        ("loan-a", "Auteur divergent"),
        ("loan-b", "Autrice"),
        ("loan-c", None),
    ]:
        source, ref = new_source(kind="loan", right=name, branch="loans")
        persist_attested_source_references(service.storage, ref)
        service.last_loans.append(replace(
            service.last_loans[0], id=name, author=author))
    before, raw = rows(service.storage), path.read_bytes()
    result = _run(private_rig)
    assert result["status"] == "partial"
    assert [(row.source.resource_id, row.comparison) for row in result["candidates"]] == [
        ("loan-1", "title_author_concordant"),
        ("loan-b", "title_author_concordant"),
        ("loan-c", "title_concordant_author_unknown"),
        ("loan-a", "title_concordant_author_divergent"),
    ]
    assert not result["coverage_complete"] and not result["fresh_network_verified"]
    assert not result["association_authorized"] and not result["provider_access_performed"]
    assert not result["may_write_origin_or_right"]
    assert calls == [] and rows(service.storage) == before and path.read_bytes() == raw
