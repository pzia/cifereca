"""#5540: Baen Free Library is a public source, not a reader account.

No live Baen network, acquisition, mutable provider rights or fake borrower.
The legacy connection key is retained for existing caller configuration.
"""
from __future__ import annotations

import pytest

from cifereca_baen import BaenSession


class NoHTTP:
    def get(self, *_args, **_kwargs):
        raise AssertionError("connect() must never issue HTTP")

    def post(self, *_args, **_kwargs):
        raise AssertionError("connect() must never issue HTTP")

    def close(self):
        pass


@pytest.mark.parametrize("credential_id,credentials", [
    ("__public__", {}),
    ("public", {}),
    ("legacy-baen", {}),
    ("legacy-profile-b", {"retired_option": "synthetic"}),
])
def test_public_free_library_never_generates_reader_account(
    credential_id, credentials,
):
    options = {"timeout_seconds": "20"}
    session = BaenSession(
        credential_id=credential_id, label="Baen",
        credentials=credentials, options=options,
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = NoHTTP()
    try:
        assert session.connect() == []
        assert session.connected is True
        assert session.accounts == []
        assert session.credential_id == credential_id
        assert session.options == options
        assert session.credentials == credentials
        assert session.connect() == []
        assert session.accounts == []
    finally:
        session.close()
    assert session.connected is False
    assert session.accounts == []


def test_legacy_key_does_not_qualify_personal_inventory_for_public_c():
    session = BaenSession(
        credential_id="legacy-baen", label="Baen",
        credentials={}, options={}, config={}, diagnostics=None,
        transport=None,
    )
    session.client.close()
    session.client = NoHTTP()
    try:
        assert session.connect() == []
        # Even if a legacy connection is configured, the source must not
        # masquerade as the canonical qualified public singleton.
        result = session.search_with_response_facets("", "catalog")
        assert result.partial is True
        assert result.results == []
    finally:
        session.close()
