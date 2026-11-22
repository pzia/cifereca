"""#4283: active BnF loans do not attest completed-loan history."""
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderSession
from cifereca_bnf_pret import BnfProviderSession, DESCRIPTOR
from test_p1_4142_bnf_validated_activity_coverage import Client


def test_installed_reader_history_is_explicitly_unsupported_not_empty():
    manifest = json.loads((Path(__file__).resolve().parents[1]
                           / "cifereca-plugin.json").read_text())
    assert DESCRIPTOR.capabilities.history is False
    assert all("history" not in capability for plugin in manifest["plugins"]
               for capability in plugin["capabilities"])
    assert BnfProviderSession.history is ProviderSession.history
    client = Client()
    session = BnfProviderSession(client=client, credential_id="credential-a",
                                 label="Synthetic", credentials={
                                     "username": "synthetic", "password": "synthetic",
                                 }, options={})
    (account,) = session.connect()
    account.identity_id = "identity-a"
    try:
        assert session.loan_inventory_snapshot(account).loans
        before = list(client.calls)
        with pytest.raises(ProviderCapabilityError, match="history unsupported"):
            session.history(account)
        assert client.calls == before
    finally:
        session.close()
