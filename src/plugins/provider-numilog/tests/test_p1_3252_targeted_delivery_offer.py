"""#3252 Numilog: exact signed card offers without broad signed-link scans.

Real HTML parser and provider adapter, fake MesLivres HTTP only. No ACSM
GET, fulfillment, filesystem publication, Core SQL, or implicit consent.
"""
from dataclasses import asdict
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_numilog_owned_js_delivery_p0 import (
    _card, _page, SIGNED, SECOND,
)
from tests.test_p1_3252_rights_observation_snapshot import (
    bound_account, session,
)


def rig(html=None):
    s, broker = session()
    html = html if html is not None else _page(
        _card(12, 9001, SIGNED), _card(34, 9002, SECOND),
    )
    reads = []
    def read(url, **_):
        reads.append(url)
        return url, html
    s._page = read
    return s, broker, reads


def test_numilog_selected_offer_is_real_and_private_link_is_not_exported():
    s, broker, reads = rig()
    account = bound_account(s)
    try:
        initial = s.rights_observation_snapshot(account)
        assert len(initial.rights) == 2
        assert all(r.delivery == "unknown" for r in initial.rights)
        assert reads == [s.library_url]
        selected = initial.rights[0]
        qualified = s.rights_delivery_offer_snapshot(account, selected.right_id)
        assert reads == [s.library_url] * 3
        assert len(qualified.rights) == 1
        right = qualified.rights[0]
        assert right == qualified.exact(selected.edition_id, selected.right_id, "purchase")
        assert right.state == "active" and right.delivery == "available"
        assert right.delivery_offer_id.startswith("numilog.card:")
        assert qualified.authorizes_mutation is False
        assert qualified.authorizes_negative_reconciliation is False
        assert qualified.status == qualified.coverage == "partial"
        exported = json.dumps(asdict(qualified))
        assert SIGNED not in exported and SECOND not in exported
        assert "SYNTHETIC_NOT_A_CREDENTIAL" not in exported
    finally:
        s.close()


@pytest.mark.parametrize("html", [
    _page(_card(12, 9001, SIGNED).replace("<script>", "<script-disabled>")
          .replace("</script>", "</script-disabled>")),
    _page(_card(12, 9001, SIGNED.replace("https://", "http://", 1))),
    _page(_card(12, 9001, SIGNED), outside_script=(
        "<script>window.extra={ProductId:9001}</script>"
    )).replace("data-product-id=\"9001\"", "data-product-id=\"9002\""),
])
def test_numilog_unqualified_card_keeps_offer_unknown(html):
    s, broker, reads = rig(html)
    try:
        account = bound_account(s)
        baseline = s.rights_observation_snapshot(account)
        assert len(baseline.rights) == 1
        chosen = baseline.rights[0]
        qualified = s.rights_delivery_offer_snapshot(account, chosen.right_id)
        assert len(qualified.rights) == 1
        assert qualified.rights[0].delivery == "unknown"
        assert qualified.rights[0].delivery_offer_id == ""
        assert reads == [s.library_url] * 3
    finally:
        s.close()


def test_numilog_unobserved_right_makes_no_extra_targeted_get():
    s, broker, reads = rig()
    try:
        account = bound_account(s)
        with pytest.raises(ProviderCapabilityError, match="numilog_offer_unobserved"):
            s.rights_delivery_offer_snapshot(
                account, "numilog:" + s.scope + ":999:unverified",
            )
        assert reads == [s.library_url]  # positive scan only
    finally:
        s.close()


@pytest.mark.parametrize("phase", ["first", "second"])
def test_numilog_rebind_between_or_during_offer_reads_fails_closed(phase):
    s, broker, reads = rig(_page(_card(12, 9001, SIGNED)))
    try:
        account = bound_account(s)
        right = s.rights_observation_snapshot(account).rights[0]
        reads.clear()
        real_page = s._page
        def modified(url, **kwargs):
            result = real_page(url, **kwargs)
            if len(reads) == (1 if phase == "first" else 2):
                broker.owner = "identity-B"
            return result
        s._page = modified
        with pytest.raises(ProviderCapabilityError):
            s.rights_delivery_offer_snapshot(account, right.right_id)
        assert reads == [s.library_url] * (1 if phase == "first" else 2)
    finally:
        s.close()


def test_numilog_exact_offer_required_before_any_signed_download(tmp_path):
    s, broker, reads = rig(_page(_card(12, 9001, SIGNED)))
    try:
        account = bound_account(s)
        right = s.rights_observation_snapshot(account).rights[0]
        observed = s.rights_delivery_offer_snapshot(account, right.right_id).rights[0]
        item = s.library()[0]
        called = []
        s._download = lambda *args, **kwargs: (
            called.append((args, kwargs)) or (tmp_path / "fake.epub", s.library_url)
        )
        s.acquisition_broker = object()  # only to pass the existing ACSM admission check
        with pytest.raises(ProviderCapabilityError, match="numilog_offer_changed"):
            s.download_direct(
                item, tmp_path, expected_offer_id="numilog.card:" + "0" * 64,
                expected_identity_id="identity-A",
            )
        assert called == []
        assert s.download_direct(
            item, tmp_path, expected_offer_id=observed.delivery_offer_id,
            expected_identity_id="identity-A",
        ) == (tmp_path / "fake.epub", s.library_url)
        assert len(called) == 1
        assert called[0][1]["authorized_link"] == SIGNED
    finally:
        s.close()


def test_numilog_foreign_identity_offer_refused_before_private_meslivres_get(
    tmp_path,
):
    s, broker, reads = rig(_page(_card(12, 9001, SIGNED)))
    try:
        account = bound_account(s)
        right = s.rights_observation_snapshot(account).rights[0]
        offered = s.rights_delivery_offer_snapshot(
            account, right.right_id,
        ).rights[0]
        item = s.library()[0]
        before = len(reads)
        broker.owner = "identity-B"
        with pytest.raises(ProviderCapabilityError, match="numilog_offer_owner_changed"):
            s.download_direct(
                item, tmp_path, expected_offer_id=offered.delivery_offer_id,
                expected_identity_id="identity-A",
            )
        assert len(reads) == before
        assert not list(tmp_path.iterdir())
    finally:
        s.close()


def test_numilog_offer_requires_independent_core_identity_not_just_opaque_id(tmp_path):
    s, broker, reads = rig(_page(_card(12, 9001, SIGNED)))
    try:
        account = bound_account(s)
        right = s.rights_observation_snapshot(account).rights[0]
        offer = s.rights_delivery_offer_snapshot(account, right.right_id).rights[0]
        item = s.library()[0]
        before = len(reads)
        with pytest.raises(ProviderCapabilityError, match="numilog_offer_unobserved"):
            s.download_direct(
                item, tmp_path, expected_offer_id=offer.delivery_offer_id,
            )
        assert len(reads) == before
    finally:
        s.close()
