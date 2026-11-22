from contextlib import closing

from cifereca.core.notice_c_loan_enrichment import request_existing_loan_cache_repairs
from cifereca.core.notice_c_initial_publication import request_initial_publication
from cifereca.core.notice_c_cover_index import canonical_cover_document_ref, read_consolidated_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6397_authenticated_web_choices import Host
from tests.test_core_p0_6366_borrower_public_c_lists_detail import loan, borrower_descriptors
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_existing_loan_only_repairs_independent_exact_public_cache(tmp_path):
    with closing(Storage(tmp_path / "loans.sqlite")) as db:
        host = Host(db)
        host._account_context_epoch = 3
        account = host.account("alice", provider="bnf-pret")
        active = loan(source_credential=account.credential_id,
                      source_account_id=account.unique_id, state="active")
        source = persist(db, verified_public(
            source_provider="bnf-pret", docbase="BNF_PRET",
            record_id=active.record_id, title=active.title, author=active.author,
        ))
        document = canonical_cover_document_ref(source.observation.key.edition_id)
        assert read_consolidated_notice_cover(db, document) is None

        def request(rows):
            return request_existing_loan_cache_repairs(
                host, rows, identity_id="alice", account_epoch=3, source_epoch=1,
                descriptors=borrower_descriptors(), now=200,
            )

        for other in (loan(state="active", source_credential=account.credential_id,
                           source_account_id="other"),
                      loan(state="returned", source_credential=account.credential_id,
                           source_account_id=account.unique_id),
                      loan(state="active", source_credential=account.credential_id,
                           source_account_id=account.unique_id, title="Other")):
            assert request([other]) == ()
        ref, = request([active, active])
        assert request([active]) == (ref,)
        assert request_initial_publication(db, source, now=200) == ref
        assert read_consolidated_notice_cover(db, document) is None
        host._source_context_epoch += 1
        assert request([active]) == ()
        assert db.entitlements() == db.acquisitions() == db.placements() == []
