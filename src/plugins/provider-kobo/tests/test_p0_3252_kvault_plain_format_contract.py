"""#3252: consume real KV v2.3.3 plain/adobe wire fields, not mock-only aliases.

KV normalizes `media_format` but retains Kobo's raw `DRMType` and
`UrlFormat`/`Format` as `raw_type`/`source_format`. No external HTTP calls
or credentials; every download is staged through the existing test transport.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    descriptor, setup, SIGNED,
)
from tests.test_p1_3252_kobo_selected_candidate import (
    epub_bytes, fake_public_client, admitted, execute, ACSM,
)


SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1",
)


def _candidate(*, drm_type="None", source_format="EPUB3",
               media_format="epub", family="none", protected=False,
               kind="http", handling="http_plain"):
    return {
        "kind": kind, "handling": handling, "transport": "https",
        "method": "GET", "url": SIGNED,
        "media_format": media_format, "source_format": source_format,
        "drm": {
            "raw_type": drm_type, "family": family,
            "protected": protected,
        },
        "drm_type": drm_type, "format": source_format,
        "protected": protected,
        "kobo": {"source": "content_access_book"},
    }


@pytest.mark.parametrize(("drm_type","source_format"), [
    ("None","EPUB3"), ("NoDrm","EPUB"), ("DRMFree","FLEPUB"),
    ("SignedNoDrm","EPUB3FL"), ("SignedNoDrm","EPUB"),
])
def test_real_kvault_plain_source_variants_are_full_eligible_offers(
    drm_type, source_format,
):
    candidate = _candidate(drm_type=drm_type,source_format=source_format)
    offer = exact_kobo_offer_id(
        descriptor(candidate=candidate), **SCOPE,
    )
    assert offer.startswith("kobo.descriptor:")


@pytest.mark.parametrize(("source_format","drm_type","family","protected"), [
    ("EPUB3FL_SAMPLE","None","none",False),
    ("EPUB_SAMPLE","SignedNoDrm","none",False),
    ("EPUB3UNKNOWN","SignedNoDrm","none",False),
    ("PDF","None","none",False),
    ("ACSM","None","none",False),
    ("KEPUB","None","none",False),
    ("EPUB3","None","adobe_adept",False),
    ("EPUB3","None","none",True),
    ("EPUB3","AdobeDrm","adobe_adept",True),
])
def test_sample_foreign_format_and_contradictory_drm_never_become_full_offer(
    source_format, drm_type, family, protected,
):
    candidate = _candidate(
        source_format=source_format, drm_type=drm_type,
        family=family, protected=protected,
    )
    assert exact_kobo_offer_id(
        descriptor(candidate=candidate), **SCOPE,
    ) == ""


def test_kvault_none_epub3_selected_offer_stages_real_epub_bytes(
    tmp_path, monkeypatch,
):
    session, vault, _broker, account, posts = setup()
    observed_gets = []
    # Compare the exact HTTP fixture bytes: a regenerated ZIP embeds a
    # fresh DOS timestamp and can differ across a two-second boundary.
    expected_epub = epub_bytes()
    def acquisition(product_id, **options):
        posts.append((product_id, options))
        return descriptor(
            candidate=_candidate(drm_type="None",source_format="EPUB3"),
        )
    vault.acquisition = acquisition
    fake_public_client(monkeypatch, lambda request: (
        observed_gets.append(str(request.url))
        or httpx.Response(200,content=expected_epub,headers={
            "content-type":"application/epub+zip",
        })
    ), [])
    try:
        offer = admitted(session, account)
        assert offer.startswith("kobo.descriptor:")
        received, _ = execute(session, account, offer, tmp_path)
        assert received.read_bytes() == expected_epub
        assert observed_gets == [SIGNED]
        assert len(posts) == 2
    finally:
        session.close()


def test_kvault_adobe_raw_label_stages_acsm_with_adept_still_in_core(
    tmp_path, monkeypatch,
):
    session, vault, _broker, account, posts = setup()
    seen = []
    def acquisition(product_id, **options):
        posts.append((product_id, options))
        return descriptor(candidate=_candidate(
            drm_type="Adobe",source_format="ACSM",
            media_format="acsm",family="adobe_adept",protected=True,
            kind="acsm",handling="adobe_adept",
        ))
    vault.acquisition = acquisition
    fake_public_client(monkeypatch, lambda request: (
        seen.append(str(request.url))
        or httpx.Response(200,content=ACSM,headers={
            "content-type":"application/vnd.adobe.adept+xml",
        })
    ), [])
    try:
        offer = admitted(session, account)
        assert offer.startswith("kobo.descriptor:")
        received, _ = execute(session, account, offer, tmp_path)
        assert received.suffix == ".acsm"
        assert received.read_bytes() == ACSM
        assert seen == [SIGNED] and len(posts) == 2
    finally:
        session.close()
