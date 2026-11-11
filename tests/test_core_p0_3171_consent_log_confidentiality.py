"""P0 #3171: exceptional consent and local receipt reads never print secrets."""

import logging
from types import SimpleNamespace

from cifereca.models import Account
from cifereca.core import source_auto_consent_bridge, source_sync_consent
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope


SECRET = "https://supplier.example.invalid/private?token=PRIVATE_SIGNED_BEARER"
SCOPE = ProviderRightsScope("PRIVATE_IDENTITY", "numilog", "PRIVATE_CREDENTIAL", "PRIVATE_BORROWER")


class BrokenStorage:
    def metadata_get(self, _key):
        raise OSError("private provider storage: " + SECRET)


class BrokenTransaction:
    def execute(self, *_args, **_kwargs):
        raise RuntimeError("private transaction: " + SECRET)


def _assert_redacted(caplog):
    for private in (SECRET, "PRIVATE_SIGNED_BEARER", "PRIVATE_IDENTITY",
                    "PRIVATE_CREDENTIAL", "PRIVATE_BORROWER", "private transaction:"):
        assert private not in caplog.text


def test_unreadable_generic_policy_disables_legacy_acquisition_without_leaking(caplog):
    account = Account(
        identity_id=SCOPE.identity_id, provider_id=SCOPE.provider_id,
        credential_id=SCOPE.credential_id, unique_id=SCOPE.account_id,
    )
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        configured = source_sync_consent.configured(
            SimpleNamespace(storage=BrokenStorage()), account,
        )
    assert configured is True  # Fail closed: do not fall back to old grants.
    assert "policy read failed code=policy_unavailable" in caplog.text
    _assert_redacted(caplog)


def test_unreadable_generation_and_transaction_keep_closed_status(caplog):
    source = SourceKey(SCOPE, "PRIVATE_EDITION", "PRIVATE_RIGHT", "purchase", "purchases")
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        observed = source_auto_consent_bridge.consent_generation(
            BrokenStorage(), SCOPE,
        )
        committed = source_auto_consent_bridge.consent_generation_in_transaction(
            BrokenTransaction(), source,
        )
    assert observed == committed == "unavailable"
    assert "generation read failed code=policy_unavailable" in caplog.text
    assert "transaction generation read failed code=policy_unavailable" in caplog.text
    _assert_redacted(caplog)


def test_legacy_source_consent_read_fails_closed_without_private_exception(caplog):
    scope = ProviderRightsScope(
        "PRIVATE_IDENTITY", "bnf-pret", "PRIVATE_CREDENTIAL",
        "PRIVATE_BORROWER",
    )
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        mode, revision = source_auto_consent_bridge.legacy_source_auto_policy(
            BrokenStorage(), scope, "loan",
        )
    assert mode == "off" and revision is None
    _assert_redacted(caplog)
