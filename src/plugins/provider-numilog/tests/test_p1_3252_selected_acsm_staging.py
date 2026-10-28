"""#3252: Core-selected Numilog ACSM remains a raw staging candidate.

Exercises the real HTTP streaming path with a synthetic signed response.
Neither the legacy acquisition broker nor Core's final ledger is invoked.
"""
from pathlib import Path

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_numilog_signed_acsm_transport_p0 import (
    ACSM, READ_BOOK, SIGNED, setup,
)
from tests.test_p1_3252_selected_delivery_candidate import (
    setup as selected_setup, selected,
)


def transport(seen, on_signed=None):
    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == READ_BOOK:
            return httpx.Response(302, headers={"location": SIGNED})
        if url == SIGNED:
            if on_signed is not None:
                on_signed()
            return httpx.Response(
                200, content=ACSM,
                headers={"content-type": "application/vnd.adobe.adept+xml"},
            )
        raise AssertionError("unselected download")
    return handler


def test_selected_acsm_stays_in_private_staging_without_legacy_adept(tmp_path):
    seen = []
    session, diagnostics, acquisition = setup(
        tmp_path, transport(seen),
    )
    attempt = tmp_path / "core-selected"
    attempt.mkdir()
    try:
        received, source = session._download(
            READ_BOOK, attempt / "selected.epub",
            stage_acsm_only=True,
        )
        assert received.is_file() and received.suffix == ".acsm"
        assert received.parent == attempt
        assert received.read_bytes() == ACSM
        assert source == session.library_url
        assert seen == [READ_BOOK, SIGNED]
        assert acquisition.calls == []
        assert not list(attempt.glob("*.part"))
        assert not list(attempt.glob("*.epub"))
    finally:
        session.close()


def test_staged_acsm_owner_change_cleans_temporary_bytes(tmp_path):
    seen = []
    session, diagnostics, acquisition = setup(tmp_path, transport(seen))
    session.client.close()
    attempt = tmp_path / "core-selected"
    attempt.mkdir()
    session.client = httpx.Client(
        transport=httpx.MockTransport(
            transport(seen, lambda: setattr(
                session.identity_broker, "identities", ("reader-B",),
            )),
        ),
        trust_env=False, follow_redirects=False,
    )
    try:
        with pytest.raises(ProviderCapabilityError):
            session._download(
                READ_BOOK, attempt / "selected.epub",
                stage_acsm_only=True,
            )
        assert acquisition.calls == []
        assert list(attempt.iterdir()) == []
    finally:
        session.close()


def test_stage_option_without_exact_offer_rejected_before_private_read(tmp_path):
    session, account, broker, reads, right = selected_setup()
    try:
        item = session.library()[0]
        before = list(reads)
        with pytest.raises(ProviderCapabilityError, match="candidate_requires_offer"):
            session.download_direct(
                item, tmp_path, stage_acsm_only=True,
            )
        assert reads == before
    finally:
        session.close()
