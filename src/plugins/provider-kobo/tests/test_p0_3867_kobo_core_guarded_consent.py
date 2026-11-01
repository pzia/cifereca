"""P0 #3867/#3171: Kobo composes an ephemeral Core policy closure at each hop.

Synthetic Kobo Vault and HTTPX responses only. No live supplier or Core grant.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, epub_bytes, fake_public_client,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    SIGNED, setup,
)

PARTNER = "https://public-supplier.test/reader.epub"


def guarded(session, account, offer, tmp_path, admission_current):
    return session.downloadable_selected_delivery_candidate_guarded(
        account, edition_id="book-1", resource_id="book-1",
        kind="purchase", branch="personal_manifest",
        selected_offer_id=offer, target_dir=tmp_path,
        explicit_user_action=True, admission_current=admission_current,
    )


def test_selected_v2_guarded_candidate_composes_core_and_provider_guards(
    tmp_path, monkeypatch,
):
    session, _vault, _broker, account, _posts = setup()
    reads, checks = [], []
    fake_public_client(monkeypatch, lambda request: (
        reads.append(str(request.url))
        or httpx.Response(200, content=epub_bytes(),
                          headers={"content-type": "application/epub+zip"})
    ), [])
    try:
        offer = admitted(session, account)
        def core():
            checks.append("core")
            return True
        candidate = guarded(session, account, offer, tmp_path, core)
        assert candidate.source.scope.identity_id == account.identity_id
        assert candidate.source.resource_id == "book-1"
        assert candidate.selected_offer_id == offer
        assert candidate.path.read_bytes() == epub_bytes()
        assert reads == [SIGNED] and len(checks) >= 5
    finally:
        session.close()


@pytest.mark.parametrize("denied", [False, None, 1, "true", "exception"])
def test_revoked_core_consent_never_reaches_private_post_or_get(
    tmp_path, monkeypatch, denied,
):
    session, _vault, _broker, account, posts = setup()
    fake_public_client(monkeypatch, lambda _request: pytest.fail(
        "GET without Core consent"
    ), [])
    try:
        offer = admitted(session, account)
        count = len(posts)
        def core():
            if denied == "exception":
                raise ValueError("private reason")
            return denied
        with pytest.raises(ProviderCapabilityError, match="kobo_candidate_admission_changed"):
            guarded(session, account, offer, tmp_path, core)
        assert len(posts) == count
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_revoked_after_first_redirect_refuses_second_supplier_get(
    tmp_path, monkeypatch,
):
    session, _vault, _broker, account, posts = setup()
    allowed = [True]
    requests = []
    def handle(request):
        requests.append(str(request.url))
        assert str(request.url) == SIGNED
        allowed[0] = False
        return httpx.Response(302, headers={"location": PARTNER})
    fake_public_client(monkeypatch, handle, [])
    try:
        offer = admitted(session, account)
        with pytest.raises(ProviderCapabilityError, match="kobo_candidate_admission_changed"):
            guarded(session, account, offer, tmp_path, lambda: allowed[0])
        assert requests == [SIGNED]
        assert list(tmp_path.iterdir()) == []
        assert len(posts) == 2
    finally:
        session.close()


def test_core_consent_off_on_aba_during_redirect_does_not_resume_old_get(
    tmp_path, monkeypatch,
):
    session, _vault, _broker, account, _posts = setup()
    revision = [1]
    expected = revision[0]
    requests = []
    def handle(request):
        requests.append(str(request.url))
        assert str(request.url) == SIGNED
        revision[0] += 1    # revoked OFF
        revision[0] += 1    # re-enabled ON with a new revision
        return httpx.Response(307, headers={"location": PARTNER})
    fake_public_client(monkeypatch, handle, [])
    try:
        offer = admitted(session, account)
        with pytest.raises(ProviderCapabilityError, match="kobo_candidate_admission_changed"):
            guarded(session, account, offer, tmp_path, lambda: revision[0] == expected)
        assert requests == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_core_callback_exception_mid_stream_prevents_candidate(
    tmp_path, monkeypatch,
):
    session, _vault, _broker, account, _posts = setup()
    allowed = [True]
    requests = []
    class RevokedDuringStream(httpx.SyncByteStream):
        def __iter__(self):
            payload = epub_bytes()
            yield payload[:12]
            allowed[0] = False  # concurrent Core OFF after first network block
            yield payload[12:]
    fake_public_client(monkeypatch, lambda request: (
        requests.append(str(request.url))
        or httpx.Response(200, stream=RevokedDuringStream(),
                          headers={"content-type": "application/epub+zip"})
    ), [])
    try:
        offer = admitted(session, account)
        def core():
            if not allowed[0]:
                raise RuntimeError("synthetic epoch invalidated")
            return True
        with pytest.raises(ProviderCapabilityError):
            guarded(session, account, offer, tmp_path, core)
        assert requests == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
