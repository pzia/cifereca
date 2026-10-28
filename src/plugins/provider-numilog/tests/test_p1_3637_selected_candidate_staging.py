"""#3637 Kobo/Numilog contract: selected Numilog bytes are staging only.

All URLs, identities and payloads are synthetic; httpx.MockTransport
prevents live provider traffic, and ADEPT/Core publication is poisoned.
"""
from pathlib import Path

import httpx
import pytest

import cifereca_numilog as provider
from cifereca.plugin_api import ProviderCapabilityError
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
    card = _card(12, 9001, SIGNED_EPUB).replace(
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
