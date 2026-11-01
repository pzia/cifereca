"""#3731: Kobo-specific display profile belongs to the account descriptor.

Only synthetic, offline objects: no network, Kobo credentials or acquisition.
"""
from __future__ import annotations

from types import SimpleNamespace

from cifereca_kobo import DESCRIPTOR, KoboSession


def test_kobo_profile_is_a_declarative_account_option_with_legacy_fallback():
    fields = {field.key: field for field in DESCRIPTOR.account_options}
    assert set(fields) == {"base_url", "display_profile"}
    option = fields["display_profile"]
    assert option.label == "DisplayProfile Kobo-Vault (Android/Desktop)"
    assert option.default == "Android"
    assert option.legacy_config_key == "kobo_backup.display_profile"
    assert option.required is False
    assert option.secret is False


def _session(options, config):
    return KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="synthetic-account", label="Synthetic",
        credentials={}, options=options, config=config,
        storage={}, diagnostics=SimpleNamespace(add=lambda *_a, **_k: None),
    )


def test_kobo_per_account_profile_overrides_legacy_global_without_mutation():
    old = {"kobo_backup.display_profile": "Desktop"}
    session = _session({"display_profile": "Android"}, old)
    assert session.display_profile == "Android"
    assert old == {"kobo_backup.display_profile": "Desktop"}
    assert session.options["display_profile"] == "Android"


def test_kobo_old_profile_is_preserved_as_runtime_fallback_not_consent():
    old = {"kobo_backup.display_profile": "Desktop",
           "kobo_backup.enabled": False}
    session = _session({}, old)
    assert session.display_profile == "Desktop"
    assert session.options == {}
    assert old["kobo_backup.enabled"] is False


def test_invalid_profile_is_not_promoted_into_a_new_mode():
    session = _session({"display_profile": "unknown"},
                       {"kobo_backup.display_profile": "Desktop"})
    assert session.display_profile == "Android"
