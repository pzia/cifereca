"""#3723: a selected BnF ACSM cannot stage through a symlinked ancestor.

Preflight must reject before reading authenticated loan inventory or issuing GETs.
"""
import pytest

from cifereca_bnf_pret import BnfProviderSession
from cifereca.plugin_api import ProviderCapabilityError


class NoNetwork:
    def __init__(self):
        self.calls = []

    def login(self, username, password):
        self.calls.append("login")
        return '<section class="active-loans"></section>'

    def activity(self):
        self.calls.append("activity")
        raise AssertionError("No authenticated request allowed")

    def clear(self):
        self.calls.append("clear")

    def close(self):
        self.calls.append("close")


def test_symlinked_parent_refused_before_selected_offer_http(tmp_path):
    real_parent = tmp_path / "real"
    staging = real_parent / "stage"
    staging.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)
    client = NoNetwork()
    session = BnfProviderSession(
        client=client, credential_id="synthetic-credential", label="Synthetic BnF",
        credentials={"username": "synthetic", "password": "synthetic"}, options={},
    )
    (account,) = session.connect()
    account.identity_id = "synthetic-identity"
    try:
        with pytest.raises(ProviderCapabilityError, match="bnf_selected_delivery_target_invalid"):
            session.rights_selected_delivery_candidate(
                account, edition_id="synthetic-edition", right_id="synthetic-loan",
                kind="loan", branch="bnf_html", delivery_offer_id="synthetic-offer",
                target_dir=alias / "stage", explicit_user_action=True,
            )
        assert client.calls == ["login"]
    finally:
        session.close()
