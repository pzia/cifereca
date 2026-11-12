"""#5055: shared borrower and follow-up UI leases must pin the exact source."""
from types import SimpleNamespace as NS

from cifereca.gui.bnf_borrow_controller import _capture_borrow_ui_scope


def _context():
    account = NS(
        identity_id="identity-a", provider_id="paris",
        credential_id="card-a", unique_id="", is_primary=True,
    )
    item = NS(
        id="row-a", record_id="edition-a", docbase="DILICOM",
        medium="digital", source_provider="paris",
        source_credential="card-a", source_collection="search",
        title="A title",
    )
    session = NS(connected=True, _epoch=4)
    service = NS(
        current_identity=NS(identity_id="identity-a"),
        accounts=[account], _account_context_epoch=9,
        _session_for_source=lambda provider, credential: (
            session if (provider, credential) == ("paris", "card-a") else None
        ),
    )
    window = NS(
        service=service, _closed=False,
        _identity_context_generation=2, _account_context_generation=3,
        _selected_search_id="row-a", _search_detail_request_generation=7,
    )
    return window, item, account, session


def test_shared_lease_requires_exact_provider_before_queuing():
    window, item, account, session = _context()
    assert _capture_borrow_ui_scope(
        window, item, account, session, provider_id="bnf-pret",
    ) is None
    item.source_provider = "bnf-pret"
    assert _capture_borrow_ui_scope(
        window, item, account, session, provider_id="bnf-pret",
    ) is None
    assert _capture_borrow_ui_scope(
        window, item, account, session, provider_id="paris",
    ) is None


def test_shared_lease_revokes_context_without_provider_io():
    window, item, account, session = _context()
    lease = _capture_borrow_ui_scope(
        window, item, account, session, provider_id="paris",
    )
    assert lease is not None
    identity_id, epoch, current = lease
    assert (identity_id, epoch) == ("identity-a", 9)
    assert current() is True
    item.title = "Different edition"
    assert current() is False
    item.title = "A title"
    window._identity_context_generation += 1
    assert current() is False


def test_shared_lease_rejects_wrong_credential_and_revokes_rebound_session():
    window, item, account, session = _context()
    item.source_credential = "other-card"
    assert _capture_borrow_ui_scope(
        window, item, account, session, provider_id="paris",
    ) is None
    item.source_credential = "card-a"
    lease = _capture_borrow_ui_scope(
        window, item, account, session, provider_id="paris",
    )
    assert lease is not None
    current = lease[2]
    assert current() is True
    account.credential_id = "other-card"
    assert current() is False
    account.credential_id = "card-a"
    session._epoch += 1
    assert current() is False


def test_after_manual_abi_cutover_all_exports_are_live():
    from cifereca.gui import bnf_borrow_controller as controller
    assert controller.__all__ == ["present_paris_borrow_verification"]
    assert all(callable(getattr(controller, key)) for key in controller.__all__)
