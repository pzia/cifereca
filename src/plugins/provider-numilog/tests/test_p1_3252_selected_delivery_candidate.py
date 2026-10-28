"""#3252: selected owned Numilog delivery reuses the scoped, signed download path.

Fixtures contain only synthetic MesLivres HTML and no real signed GET.
Core admission, persistent consent, SHA and publication are not simulated.
"""
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_targeted_delivery_offer import rig
from tests.test_p1_3252_rights_observation_snapshot import bound_account
from tests.test_numilog_owned_js_delivery_p0 import SIGNED, _card, _page


def setup():
    session, broker, reads = rig(_page(_card(12, 9001, SIGNED)))
    account = bound_account(session)
    right = session.rights_delivery_offer_snapshot(
        account, session.rights_observation_snapshot(account).rights[0].right_id,
    ).rights[0]
    return session, account, broker, reads, right


def selected(right, target_dir, **changes):
    return dict(
        edition_id=right.edition_id, right_id=right.right_id,
        kind="purchase", branch="meslivres",
        delivery_offer_id=right.delivery_offer_id,
        target_dir=target_dir, explicit_user_action=True,
        **changes,
    )


def test_selected_numilog_executes_existing_offer_guard_once(tmp_path):
    session, account, broker, reads, right = setup()
    try:
        session.acquisition_broker = object()
        observed = []
        session._download = lambda *args, **kw: (
            observed.append((args, kw)) or (tmp_path / "staged.epub", session.library_url)
        )
        outcome = session.rights_selected_delivery_candidate(
            account, **selected(right, tmp_path),
        )
        assert outcome == (tmp_path / "staged.epub", session.library_url)
        assert len(observed) == 1
        assert observed[0][1]["authorized_link"] == SIGNED
        assert observed[0][1]["expected_owner_scope"] is not None
        assert all(url == session.library_url for url in reads)
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    {"explicit_user_action": False},
    {"edition_id": "foreign-edition"},
    {"kind": "loan"},
    {"branch": "other"},
    {"delivery_offer_id": "numilog.card:" + "X" * 64},
    {"delivery_offer_id": "numilog.card:" + "0" * 63},
])
def test_numilog_selected_candidate_bad_request_before_private_get(
    tmp_path, change,
):
    session, account, broker, reads, right = setup()
    try:
        options = selected(right, tmp_path)
        options.update(change)
        before = list(reads)
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_mismatch"):
            session.rights_selected_delivery_candidate(account, **options)
        assert reads == before
    finally:
        session.close()


def test_numilog_selected_candidate_foreign_owner_no_private_get(tmp_path):
    session, account, broker, reads, right = setup()
    try:
        broker.owner = "identity-B"
        before = list(reads)
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert reads == before
    finally:
        session.close()


def test_numilog_selected_candidate_owner_swap_during_library_stops_before_download(tmp_path):
    session, account, broker, reads, right = setup()
    try:
        original = session.library
        def switched():
            result = original()
            broker.owner = "identity-B"
            return result
        session.library = switched
        session._download = lambda *a, **kw: pytest.fail("foreign signed GET")
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_owner_changed"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
    finally:
        session.close()


def test_numilog_selected_candidate_rotated_or_foreign_offer_blocks_signed_get(tmp_path):
    session, account, broker, reads, right = setup()
    try:
        session._download = lambda *a, **kw: pytest.fail("signed GET must be refused")
        with pytest.raises(ProviderCapabilityError, match="numilog_offer_changed"):
            session.rights_selected_delivery_candidate(
                account,
                **selected(right, tmp_path,
                           delivery_offer_id="numilog.card:" + "0" * 64),
            )
    finally:
        session.close()
