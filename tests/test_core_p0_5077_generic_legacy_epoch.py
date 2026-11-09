"""P0 #5077: one legacy consent policy defines toggle and ABA epoch keys.

This adapter does not grant any new acquisition consent.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import source_auto_consent_bridge as bridge


def scope(provider):
    return NS(identity_id="alice", provider_id=provider,
              credential_id="credential", account_id="account")


@pytest.mark.parametrize(("provider", "expected"), [
    ("numilog", "sync.source.numilog.materialization.epoch.v1."),
    ("kobo", "sync.source.materialization.on_start."),
    ("bnf-pret", "sync.source.bnf_auto."),
])
def test_legacy_epoch_uses_same_provider_mapping_as_toggle(provider, expected):
    source = scope(provider)
    sid = bridge._source_id(source)
    key = bridge._consent_generation_key(source)
    suffix = "" if provider == "numilog" else ".revision"
    assert key == expected + sid + suffix


def test_future_provider_policy_is_generic_but_unknown_and_ambiguous_fail_closed(monkeypatch):
    source = scope("fixture-provider")
    assert bridge._consent_generation_key(source) is None
    policies = dict(bridge._POLICIES)
    policies[("fixture-provider", "purchase")] = (
        "compat.fixture.", "enabled", "compat.epoch.fixture."
    )
    monkeypatch.setattr(bridge, "_POLICIES", policies)
    assert bridge._consent_generation_key(source) == (
        "compat.epoch.fixture." + bridge._source_id(source)
    )
    policies[("fixture-provider", "loan")] = ("compat.fixture.loan.", "enabled", None)
    assert bridge._consent_generation_key(source) is None


def test_epoch_key_has_no_provider_name_dispatch():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(bridge._consent_generation_key))
    literal_providers = {"kobo", "numilog", "bnf-pret", "paris"}
    assert not any(
        isinstance(value, ast.Constant) and value.value in literal_providers
        for value in ast.walk(tree)
    )
