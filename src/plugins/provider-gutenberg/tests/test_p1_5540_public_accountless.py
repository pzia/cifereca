"""#5540: an OPDS-only Gutenberg catalogue does not own reader accounts.

Only local fixture transports; no HTTP, account login or private capture.
"""
from __future__ import annotations

import pytest

from cifereca_gutenberg import GutenbergSession


class NoHTTP:
    def get(self, *_args, **_kwargs):
        raise AssertionError("Public catalogue connect() must remain offline")

    def post(self, *_args, **_kwargs):
        raise AssertionError("Public catalogue connect() must remain offline")

    def close(self):
        pass


@pytest.mark.parametrize("credential,credentials", [
    ("__public__", {}),
    ("historical-gutenberg", {}),
    ("old-public-row", {"unused": "not-an-auth-credential"}),
])
def test_public_gutenberg_connect_never_invents_an_upstream_borrower(
    credential, credentials,
):
    session = GutenbergSession(
        credential_id=credential, label="Gutenberg",
        credentials=credentials, options={"contact": "fixture@example.test"},
        config={}, diagnostics=None,
    )
    session.client.close()
    session.client = NoHTTP()
    try:
        assert session.connect() == []
        assert session.accounts == []
        assert session.connected is True
        assert session.credential_id == credential
        assert session.options["contact"] == "fixture@example.test"
        # Reconnecting an old saved source cannot recreate the pseudo-account.
        assert session.connect() == []
        assert session.accounts == []
    finally:
        session.close()
