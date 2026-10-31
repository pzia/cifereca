"""#3637 Kobo/Numilog contract: selected Numilog bytes are staging only.

All URLs, identities and payloads are synthetic; httpx.MockTransport
prevents live provider traffic, and ADEPT/Core publication is poisoned.
"""
from pathlib import Path

import httpx
import pytest

import cifereca_numilog as provider
from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from tests.test_p1_3252_selected_delivery_candidate import setup, selected
from tests.test_p1_3252_targeted_delivery_offer import rig
from tests.test_p1_3252_rights_observation_snapshot import bound_account
from tests.test_numilog_owned_js_delivery_p0 import _card, _page
from tests.test_numilog_not_specified_epub_confirmation_p0_2024 import (
    SIGNED_EPUB,
)
from tests.test_numilog_no_stream_drmfree_epub_p0_2024 import fake_epub_bytes


@pytest.mark.parametrize("target_kind", ["missing", "file", "symlink"])
def test_selected_staging_refuses_bad_directory_before_private_get(
    tmp_path, target_kind,
):
    session, account, broker, reads, right = setup()
    target = tmp_path / "destination"
    if target_kind == "file":
        target.write_text("not a directory")
    elif target_kind == "symlink":
        target.symlink_to(tmp_path, target_is_directory=True)
    try:
        before = list(reads)
        with pytest.raises(ProviderCapabilityError, match="staging_invalid"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, target),
            )
        assert reads == before
        assert not target.is_dir() or target.is_symlink()
    finally:
        session.close()


def _direct_epub_session():
    # The exact owned-card signature and DRM/format claim must be consistent.
    card = _card(12, 9001, SIGNED_EPUB, attrs='onclick="ModalDownload(9001, 1)"').replace(
        "DrmTechnlology: 'ADOBE', link:",
        "DrmTechnlology: 'NOT_SPECIFIED', format: 'EPUB', link:",
    )
    session, broker, reads = rig(_page(card))
    account = bound_account(session)
    rights = session.rights_observation_snapshot(account).rights
    assert len(rights) == 1
    right = session.rights_delivery_offer_snapshot(
        account, rights[0].right_id,
    ).rights[0]
    assert right.delivery == "available"
    return session, account, reads, right


def test_selected_direct_epub_is_staged_not_published(tmp_path, monkeypatch):
    session, account, reads, right = _direct_epub_session()
    payload = fake_epub_bytes()
    requested = []
    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == SIGNED_EPUB
        return httpx.Response(
            200, content=payload,
            headers={"content-type": "application/epub+zip"},
        )
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        trust_env=False, follow_redirects=False,
    )
    monkeypatch.setattr(provider, "publish", lambda *a, **kw: pytest.fail(
        "selected candidate must not publish in legacy library",
    ))
    monkeypatch.setattr(
        provider, "publish_artifact_file",
        lambda *a, **kw: pytest.fail("selected candidate must not publish"),
    )
    try:
        path, provenance = session.rights_selected_delivery_candidate(
            account, **selected(right, tmp_path),
        )
        assert path.parent == tmp_path
        assert path.suffix == ".epub"
        assert path.name.startswith(".numilog-")
        assert path.read_bytes() == payload
        assert provenance == session.library_url
        assert requested == [SIGNED_EPUB]
        assert not list(tmp_path.glob("*.part"))
        assert len(reads) >= 3
    finally:
        session.close()


def test_selected_direct_epub_rejects_acsm_response_without_legacy_adept(
    tmp_path,
):
    from tests.test_numilog_signed_acsm_transport_p0 import ACSM
    session, account, reads, right = _direct_epub_session()
    requested = []
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                requested.append(str(request.url))
                or httpx.Response(
                    200, content=ACSM,
                    headers={"content-type": "application/vnd.adobe.adept+xml"},
                )
            ),
        ),
        trust_env=False, follow_redirects=False,
    )
    try:
        with pytest.raises(ProviderCapabilityError, match="format_changed"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert requested == [SIGNED_EPUB]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


@pytest.mark.parametrize("rebind", [
    "same_scope_new_account", "foreign_identity", "disconnect",
    "provider_changed", "credential_changed", "client_replaced",
])
def test_selected_epub_post_get_session_rebind_withdraws_candidate(
    tmp_path, rebind,
):
    from dataclasses import replace
    session, account, reads, right = _direct_epub_session()
    requested = []
    def handler(request):
        requested.append(str(request.url))
        if rebind == "same_scope_new_account":
            session._accounts = [replace(session._accounts[0])]
        elif rebind == "foreign_identity":
            session._accounts[0].identity_id = "other-identity"
        elif rebind == "disconnect":
            session.connected = False
        elif rebind == "provider_changed":
            session.provider_id = "foreign-provider"
        elif rebind == "credential_changed":
            session.credential_id = "other-credential"
        elif rebind == "client_replaced":
            session.client = object()
        return httpx.Response(
            200, content=fake_epub_bytes(),
            headers={"content-type": "application/epub+zip"},
        )
    client = session.client
    client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        trust_env=False, follow_redirects=False,
    )
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert requested == [SIGNED_EPUB]
        assert list(tmp_path.iterdir()) == []
    finally:
        # Avoid a forged client during close() in the synthetic test.
        if not isinstance(session.client, httpx.Client):
            session.client = httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            )
        session.close()


def test_selected_epub_failed_http_then_explicit_retry_uses_fresh_scope(
    tmp_path,
):
    from cifereca.plugin_api import ProviderError
    session, account, reads, right = _direct_epub_session()
    calls = []
    failing = {"value": True}
    def handler(request):
        calls.append(str(request.url))
        if failing["value"]:
            return httpx.Response(
                503, content=b"SYNTHETIC_PRIVATE_BODY_NEVER_LOGGED",
            )
        return httpx.Response(
            200, content=fake_epub_bytes(),
            headers={"content-type": "application/epub+zip"},
        )
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        trust_env=False, follow_redirects=False,
    )
    try:
        with pytest.raises(ProviderError) as failed:
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert SIGNED_EPUB not in str(failed.value)
        assert "SYNTHETIC_PRIVATE_BODY_NEVER_LOGGED" not in str(failed.value)
        assert list(tmp_path.iterdir()) == []
        first_reads = len(reads)
        failing["value"] = False
        staged, _ = session.rights_selected_delivery_candidate(
            account, **selected(right, tmp_path),
        )
        assert staged.is_file() and staged.suffix == ".epub"
        assert staged.read_bytes() == fake_epub_bytes()
        assert calls == [SIGNED_EPUB, SIGNED_EPUB]
        assert len(reads) > first_reads  # no reuse of stale signed evidence
        assert len(list(tmp_path.iterdir())) == 1
    finally:
        session.close()


def test_selected_acsm_rejects_valid_epub_without_legacy_publication(
    tmp_path, monkeypatch,
):
    session, account, broker, reads, right = setup()
    seen = []
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                seen.append(str(request.url))
                or httpx.Response(
                    200, content=fake_epub_bytes(),
                    headers={"content-type": "application/epub+zip"},
                )
            ),
        ),
        trust_env=False, follow_redirects=False,
    )
    monkeypatch.setattr(provider, "publish", lambda *a, **kw: pytest.fail(
        "ACSM offer cannot publish a fallback EPUB",
    ))
    monkeypatch.setattr(
        provider, "publish_artifact_file",
        lambda *a, **kw: pytest.fail("ACSM offer cannot publish fallback"),
    )
    try:
        with pytest.raises(ProviderCapabilityError, match="format_changed"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert len(seen) == 1
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


@pytest.mark.parametrize(("response", "reason"), [
    (
        httpx.Response(
            302, headers={"location": (
                "https://127.0.0.1/fulfillment/another.acsm"
            )},
        ),
        "redirection réseau inattendue",
    ),
    (
        httpx.Response(
            200, text="<html><a href='/other'>Login</a></html>",
            headers={"content-type": "text/html"},
        ),
        "html_unexpected",
    ),
])
def test_selected_numilog_signed_link_never_follows_unsafe_or_html_second_get(
    tmp_path, response, reason,
):
    from tests.test_numilog_owned_js_delivery_p0 import SIGNED
    session, account, broker, reads, right = setup()
    requested = []
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                requested.append(str(request.url)) or response
            ),
        ),
        trust_env=False, follow_redirects=False,
    )
    try:
        with pytest.raises((ProviderError, ProviderCapabilityError), match=reason):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert requested == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
