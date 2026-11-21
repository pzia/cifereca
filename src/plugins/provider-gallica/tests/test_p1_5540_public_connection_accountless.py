"""#5540: a public Gallica source is never an upstream borrower.

Synthetic local fixtures; neither provider HTTP nor user account login.
Legacy saved connection IDs remain configuration keys, not invented accounts.
"""
from __future__ import annotations

import pytest

from cifereca_gallica import GallicaSession


class NoHTTP:
    def get(self, *_args, **_kwargs):
        raise AssertionError("Gallica public connect must not request a provider")

    def post(self, *_args, **_kwargs):
        raise AssertionError("Gallica public connect must not request a provider")

    def request(self, *_args, **_kwargs):
        raise AssertionError("Gallica public connect must not request a provider")

    def close(self):
        pass


@pytest.mark.parametrize("credential_id,credentials", [
    ("__public__", {}),
    ("legacy-gallica", {}),
    ("legacy-profile-b", {}),
    ("old-credential-id", {"unused": "synthetic-not-an-auth-token"}),
])
def test_public_opds_connect_never_fabricates_reader_accounts(
    credential_id, credentials,
):
    session = GallicaSession(
        credential_id=credential_id, label="Gallica", credentials=credentials,
        options={"opds_url": "https://gallica.bnf.fr/opds"},
        config={}, diagnostics=None,
    )
    session.client.close()
    session.client = NoHTTP()
    try:
        assert session.connect() == []
        assert session.accounts == []
        assert session.connected is True
        assert session.credential_id == credential_id
        assert session.options["opds_url"] == "https://gallica.bnf.fr/opds"
        # Reconnect must not bring back a stale pseudo-reader.
        assert session.connect() == []
        assert session.accounts == []
    finally:
        session.close()


def test_removing_public_runtime_pseudo_account_does_not_rewrite_saved_config(tmp_path):
    from cifereca.config import Config

    path = tmp_path / "config.json"
    config = Config(path)
    config.set("providers.accounts", [{
        "provider": "gallica",
        "id": "legacy-gallica",
        "identity_id": "default",
        "label": "Ancienne connexion publique",
        "enabled": False,
        "anonymous": True,
        "credentials": {},
        "options": {"opds_url": "https://gallica.bnf.fr/opds"},
    }])
    previous = config.provider_accounts("gallica")
    session = GallicaSession(
        credential_id="legacy-gallica", label="Gallica", credentials={},
        options=previous[0]["options"], config={}, diagnostics=None,
    )
    session.client.close()
    session.client = NoHTTP()
    try:
        assert session.connect() == []
        assert config.provider_accounts("gallica") == previous
        config.save()
        assert Config(path).provider_accounts("gallica") == previous
    finally:
        session.close()
