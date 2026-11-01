"""#3252 Numilog six synthetic purchases reach distinct staged EPUB candidates.

This is a real adapter+HTML parser+mocked signed GET test, NOT a Core
publication or an actual supplier production receipt. Core owns verified
placement, OriginLink, post-placement same-profile dedup and idempotence.
"""
from __future__ import annotations

import io
from pathlib import Path
import zipfile

import httpx
import pytest
import cifereca_numilog as provider
from tests.pinned_public_https_fixture import mock_selected_public_client_from_session

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_numilog_owned_js_delivery_p0 import _card, _page
from tests.test_numilog_not_specified_epub_confirmation_p0_2024 import SIGNED_EPUB
from tests.test_p1_3252_targeted_delivery_offer import rig
from tests.test_p1_3252_rights_observation_snapshot import bound_account
from tests.test_p1_3637_selected_candidate_staging import _direct_epub_session


def epub(index: int):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        output.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        output.writestr(
            'META-INF/container.xml',
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        output.writestr(
            'OEBPS/content.opf',
            f'<package version="3.0"><metadata>Purchase {index}</metadata></package>',
        )
    return stream.getvalue()


def six_cards():
    links = tuple(
        SIGNED_EPUB.replace("productid=9001", f"productid={9001 + i}")
        for i in range(6)
    )
    assert len(set(links)) == 6
    cards = tuple(
        _card(12 + 11 * i, 9001 + i, links[i],
              attrs=f'onclick="ModalDownload({9001+i}, 1)"').replace(
                  "DrmTechnlology: 'ADOBE', link:",
                  "DrmTechnlology: 'NOT_SPECIFIED', format: 'EPUB', link:",
              )
        for i in range(6)
    )
    return _page(*cards), links


def test_six_positive_purchases_produce_six_staged_epub_without_legacy_publication(
    tmp_path, monkeypatch,
):
    import cifereca_numilog as provider
    html, links = six_cards()
    session, broker, reads = rig(html)
    account = bound_account(session)
    payloads = dict(zip(links, [epub(i) for i in range(6)]))
    visited = []
    original_client = session.client
    original_client.close()
    def handler(request):
        url = str(request.url)
        visited.append(url)
        assert url in payloads, "No fallback, public catalogue or unrelated item GET"
        return httpx.Response(
            200, content=payloads[url],
            headers={"content-type": "application/epub+zip"},
        )
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False,
        follow_redirects=False,
    )
    monkeypatch.setattr(provider, "publish", lambda *a, **kw: pytest.fail(
        "Never publish a selected candidate using the legacy path"
    ))
    monkeypatch.setattr(provider, "publish_artifact_file", lambda *a, **kw: pytest.fail(
        "Provider must not publish canonically"
    ))
    core_clients = mock_selected_public_client_from_session(monkeypatch, provider, session)
    try:
        observed = session.rights_observation_snapshot(account)
        assert len(observed.rights) == 6
        assert len({x.edition_id for x in observed.rights}) == 6
        assert all(x.delivery == "unknown" and x.provider_attested
                   for x in observed.rights)
        retrieved = []
        for i, row in enumerate(observed.rights):
            offer = session.rights_selected_delivery_offer_snapshot(
                account, edition_id=row.edition_id, right_id=row.right_id,
                kind="purchase", branch="meslivres",
                explicit_user_action=True,
            )
            exact = offer.exact(row.edition_id, row.right_id, "purchase")
            assert exact is not None and exact.delivery_attested
            staging = tmp_path / f"core-stage-{i}"
            staging.mkdir()
            delivered, _ = session.rights_selected_delivery_candidate(
                account, edition_id=row.edition_id, right_id=row.right_id,
                kind="purchase", branch="meslivres",
                delivery_offer_id=exact.delivery_offer_id,
                target_dir=staging, explicit_user_action=True,
            )
            assert delivered.parent == staging
            assert zipfile.is_zipfile(delivered)
            assert delivered.read_bytes() == payloads[links[i]]
            assert len(list(staging.iterdir())) == 1
            retrieved.append(delivered)
        assert len(retrieved) == 6
        assert visited == list(links)  # one signed content GET per purchase
        assert len(core_clients) == 6
        assert [url for _producer, _timeout, urls in core_clients for url in urls] == list(links)
        assert len(set(path.read_bytes() for path in retrieved)) == 6
        assert all(url == session.library_url for url in reads)
    finally:
        session.close()


@pytest.mark.parametrize("payload", ["epub", "acsm"])
def test_selected_stage_inode_swap_during_signed_http_never_writes_external_file(
    tmp_path, monkeypatch, payload,
):
    from tests.test_p1_3252_selected_delivery_candidate import setup, selected
    from tests.test_numilog_signed_acsm_transport_p0 import ACSM
    if payload == "epub":
        session, account, reads, right = _direct_epub_session()
        data = epub(1)
        mime = "application/epub+zip"
    else:
        session, account, broker, reads, right = setup()
        data = ACSM
        mime = "application/vnd.adobe.adept+xml"
    stage = tmp_path / "stage"
    stage.mkdir()
    old_stage = tmp_path / "renamed-stage"
    outside = tmp_path / "outside"
    outside.mkdir()
    signed_reads = []

    def handler(request):
        signed_reads.append(str(request.url))
        stage.rename(old_stage)
        stage.symlink_to(outside, target_is_directory=True)
        return httpx.Response(200, content=data, headers={"content-type": mime})

    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False,
        follow_redirects=False,
    )
    core_clients = mock_selected_public_client_from_session(monkeypatch, provider, session)
    try:
        with pytest.raises(ProviderCapabilityError, match="numilog_candidate_transport_refused"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, stage),
            )
        assert len(signed_reads) == 1
        assert len(core_clients) == 1
        assert list(outside.iterdir()) == []
        assert list(old_stage.iterdir()) == []
    finally:
        session.close()
