"""#1844: public ADEPT refusal codes must never echo private broker data.

Only in-memory broker and synthetic account. No URL, HTTP, DRM or acquisition.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import Account, AccountRef, ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession


class Poison:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN path=/home/PRIVATE")

    def __bool__(self):
        raise RuntimeError("PRIVATE_ACCOUNT")


def _session(outcome):
    session = NumilogSession(
        credential_id="fixture", label="Synthetic",
        credentials={"email": "reader@example.test", "password": "fixture"},
        options={}, config={}, diagnostics=None,
        acquisition_broker=NS(import_acsm=lambda *a, **kw: outcome),
    )
    account = "numilog:" + hashlib.sha256(b"fixture").hexdigest()[:20]
    session.identity_broker = NS(accounts=lambda identity_id=None, **_kwargs: (
        AccountRef(account_id=account, identity_id="reader",
                   provider_id="numilog", label="Synthetic"),
    ))
    session._accounts = [Account(provider_id="numilog", credential_id=session.credential_id,
                                 unique_id=account, identity_id="reader")]
    return session


@pytest.mark.parametrize("raw_code", (
    "Bearer PRIVATE_TOKEN",
    "adept_profile_required?auth=PRIVATE_TOKEN",
    "adept_profile_ambiguous\nPRIVATE_CREDENTIAL",
    "/home/PRIVATE_USER/adept",
    True, 7, None, {"token": "PRIVATE_SECRET"}, Poison(),
))
def test_unknown_broker_refusal_codes_are_replaced_with_a_fixed_reason(raw_code):
    session = _session({"status": "attention_required", "code": raw_code})
    try:
        with pytest.raises(ProviderCapabilityError) as caught:
            session._fulfill_acsm("ignored.acsm", title="Synthetic")
        assert "adept_attention_required" in str(caught.value)
        for secret in ("PRIVATE", "Bearer", "/home/", "auth="):
            assert secret not in str(caught.value)
    finally:
        session.close()


@pytest.mark.parametrize("code", (
    "adept_profile_required", "adept_profile_ambiguous",
))
def test_known_core_adept_refusal_codes_preserve_user_action(code):
    session = _session({"status": "attention_required", "code": code})
    try:
        with pytest.raises(ProviderCapabilityError, match=code):
            session._fulfill_acsm("ignored.acsm")
    finally:
        session.close()


@pytest.mark.parametrize("invalid_status", (
    Poison(), True, 200, {"status": "complete"}, None,
))
def test_nontext_broker_status_is_never_stringified_or_treated_as_success(invalid_status):
    session = _session({
        "status": invalid_status,
        "path": "/home/PRIVATE_PATH/not-an-artifact.epub",
    })
    try:
        with pytest.raises(ProviderError) as caught:
            session._fulfill_acsm("ignored.acsm")
        assert "non confirmé" in str(caught.value)
        assert "PRIVATE" not in str(caught.value)
    finally:
        session.close()


@pytest.mark.parametrize("bad_path", (Poison(), True, 33, {"path": "PRIVATE"}, None, ""))
def test_bad_broker_output_path_cannot_be_coerced_to_an_artifact(bad_path):
    session = _session({"status": "complete", "path": bad_path})
    try:
        with pytest.raises(ProviderError) as caught:
            session._fulfill_acsm("ignored.acsm")
        assert "non confirmé" in str(caught.value)
        assert "PRIVATE" not in str(caught.value)
    finally:
        session.close()
