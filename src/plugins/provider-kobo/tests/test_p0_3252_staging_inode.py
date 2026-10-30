"""#3252 P0 Kobo selected candidates stay under the original Core stage inode.

No real Vault, network, account or Core publication. Both the descriptor POST
and the signed HTTP GET are synthetic; no signed URL enters diagnostics.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, execute, epub_bytes, fake_public_client,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    setup, descriptor,
)


@pytest.mark.parametrize("phase", ["vault_descriptor", "signed_get"])
def test_kobo_stage_directory_swap_does_not_leak_or_publish(
    tmp_path, monkeypatch, phase,
):
    session, vault, broker, account, posts = setup()
    stage = tmp_path / "core-stage"
    stage.mkdir()
    original = tmp_path / "original-stage"
    outside = tmp_path / "outside"
    outside.mkdir()
    traffic = []

    def replace_stage():
        stage.rename(original)
        stage.symlink_to(outside, target_is_directory=True)

    def handler(request):
        traffic.append(str(request.url))
        if phase == "signed_get":
            replace_stage()
        return httpx.Response(
            200, content=epub_bytes(),
            headers={"content-type": "application/epub+zip"},
        )

    fake_public_client(monkeypatch, handler, [])
    try:
        offer = admitted(session, account)
        if phase == "vault_descriptor":
            def replaced(product_id, **kwargs):
                posts.append((product_id, kwargs))
                replace_stage()
                return descriptor()
            vault.acquisition = replaced

        with pytest.raises(ProviderCapabilityError, match="kobo_candidate_staging_rebound"):
            execute(session, account, offer, stage)
        assert len(traffic) == (0 if phase == "vault_descriptor" else 1)
        assert list(original.iterdir()) == []
        assert list(outside.iterdir()) == []
    finally:
        session.close()
