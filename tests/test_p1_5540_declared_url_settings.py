"""P1 #5540: opt-in typed URL in generic Settings, no HTTP or provider calls.

All URLs, accounts and identities are fictional. Existing provider text URL
fields are *not* migrated by these tests or by the runtime change.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.config import Config
from cifereca.gui.settings_draft import build_settings_draft, SettingsValidationError
from cifereca.plugin_api import (
    AccountOptionField, CredentialField, ProviderCapabilities, ProviderDescriptor,
)
from cifereca.providers.base import valid_account_option_url


PROVIDER = "synthetic.url"
_MISSING = object()


def field(**kwargs):
    return AccountOptionField(
        "base_url", "URL du portail", default="https://api.example.test/v1",
        value_type="url", **kwargs,
    )


def descriptor(spec=None):
    return ProviderDescriptor(
        id=PROVIDER, display_name="Fournisseur fictif", description="Sans I/O",
        state="ready", capabilities=ProviderCapabilities(),
        credential_fields=(CredentialField("username", "Compte"),),
        account_options=(spec if spec is not None else field(),),
    )


def account(credential, identity, *, original=None, entered=_MISSING):
    original = original if original is not None else {"retired_flag": [True]}
    return {
        "id": credential, "provider": PROVIDER,
        "identity": identity, "label": credential, "enabled": True,
        "fields": {"username": "SYNTHETIC"},
        "original_credentials": {"username": "SYNTHETIC"},
        "original_options": deepcopy(original),
        "original_account": {
            "id": credential, "provider": PROVIDER, "identity_id": identity,
            "label": credential, "enabled": True,
            "credentials": {"username": "SYNTHETIC"},
            "options": deepcopy(original),
        },
        "option_fields": {"base_url": (
            entered if entered is not _MISSING else original.get("base_url", "")
        )},
    }


def case(tmp_path, entries, *, spec=None):
    config = Config(tmp_path / "settings.json")
    config.set("identities", [
        {"id": "alice", "directory_name": "alice"},
        {"id": "bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    values = {
        "identities": [
            {"id": owner, "label": owner, "directory": owner,
             "adept_profile": "", "languages": "fr", "prefer_vo": False}
            for owner in ("alice", "bob")
        ],
        "accounts": entries, "provider_downloads": {},
        "levels": ["normal"], "diagnostics_level_index": 0,
        "vault_profiles": ["Desktop"], "vault_profile_index": 0,
        "diagnostics_enabled": False, "experimental_actions": False,
        "backup_enabled": False, "allow_unlinked_acquisition": False,
        "backup_interval": "60", "library_directory": str(tmp_path / "books"),
        "backup_directory": "", "download_command": "",
        "post_download_command": "", "download_directory": "",
        "ebook_directory": "", "audiobook_directory": "", "ai": {},
    }
    before = deepcopy(config.data)
    try:
        result = build_settings_draft(
            config, values, descriptors={PROVIDER: descriptor(spec)},
            public_ids=set(), current_dirs={"alice": "alice", "bob": "bob"},
        )
        return result
    finally:
        assert config.data == before
        assert not config.path.exists()


def test_exact_account_urls_default_blank_and_old_opaque_fields_preserved(tmp_path):
    result = case(tmp_path, [
        account("cred-a", "alice"),  # new field: declared default
        account("cred-b", "bob", original={
            "base_url": "https://other.example.test/path", "legacy": {"opaque": 7},
        }),
    ])
    options = {row["id"]: row["options"]
               for row in result.data["providers"]["accounts"]}
    assert options["cred-a"] == {
        "retired_flag": [True], "base_url": "https://api.example.test/v1",
    }
    assert options["cred-b"] == {
        "base_url": "https://other.example.test/path",
        "legacy": {"opaque": 7},
    }
    assert [(row["id"], row["identity_id"])
            for row in result.data["providers"]["accounts"]] == [
        ("cred-a", "alice"), ("cred-b", "bob"),
    ]


def test_explicit_legacy_empty_url_stays_empty_if_optional(tmp_path):
    result = case(tmp_path, [
        account("cred-a", "alice", original={"base_url": ""}),
    ])
    options, = (row["options"]
                for row in result.data["providers"]["accounts"])
    assert options["base_url"] == ""


@pytest.mark.parametrize("bad", [
    "http://api.example.test/v1",  # only explicitly permitted schemes
    "https://user:PRIVATE@example.test/path",
    "https://user@example.test/path",
    "https://example.test/path?token=PRIVATE",
    "https://example.test/path#PRIVATE",
    "https://example.test:0/path",
    "https://example.test:99999/path",
    "https://example.test/a%0ab",
    "https://example.test/a%250ab",  # double encoded control
    "https://example.test/a%ZZ",
    "https://example.test/a\\secret",
    "https://api.example.test /path",
    "https://example.test:bad/path",
    "https://evil..example.test/path",
    "https://-evil.example.test/path",
    "https://éxample.test/path",
    "https://[::1]/path",  # first schema intentionally excludes IPv6
    "https:///no-host",
    "javascript:alert(1)",
    "https://example.test/\u202epath",
    " https://api.example.test/v1",
    "https://api.example.test/v1 ",
    "",
    None, True, {"url": "https://api.example.test"}, 42,
])
def test_url_guard_refuses_unsupported_syntax_or_untyped_values(bad):
    assert not valid_account_option_url(field(), bad)


@pytest.mark.parametrize("bad", [
    "http://api.example.test/v1", "https://user:pass@api.example.test",
    "https://api.example.test/path?auth=PRIVATE", "https://example.test/%0a",
    "javascript:alert(1)", " https://api.example.test/v1",
    None, True, 42, {"url": "https://api.example.test"},
])
def test_bad_draft_url_rejected_without_config_write(tmp_path, bad):
    with pytest.raises(SettingsValidationError):
        case(tmp_path, [account("cred-a", "alice", entered=bad)])


def test_explicit_local_http_optin_and_invalid_descriptor_refusal(tmp_path):
    local = AccountOptionField(
        "base_url", "URL locale", default="http://localhost:9000/api",
        value_type="url", url_schemes=("https", "http"),
    )
    assert valid_account_option_url(local, "http://localhost:9000/api")
    assert valid_account_option_url(local, "https://api.example.test")
    saved = case(tmp_path, [
        account("cred-a", "alice", entered="http://localhost:9000/api"),
    ], spec=local)
    assert saved.data["providers"]["accounts"][0]["options"]["base_url"] == (
        "http://localhost:9000/api"
    )
    for malformed in (
        ("https", "https"), ("http", "ftp"), ("HTTPS",), (), ["https"],
    ):
        spec = AccountOptionField(
            "base_url", "URL", default="https://api.example.test/v1",
            value_type="url", url_schemes=malformed,
        )
        assert not valid_account_option_url(spec, "https://api.example.test/v1")
        with pytest.raises(SettingsValidationError):
            case(tmp_path, [
                account("cred-a", "alice", entered="https://api.example.test/v1"),
            ], spec=spec)


def test_existing_text_option_retains_legacy_http_and_query_semantics():
    original = AccountOptionField(
        "endpoint", "Endpoint", default="http://localhost:9000/api?x=1",
    )
    assert original.value_type == "text"
    assert original.url_schemes == ("https",)
