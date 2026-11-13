"""P0 #5077: historical switch keys are metadata, never consent authority."""
from __future__ import annotations

import ast
import inspect

import pytest

from cifereca.core import source_sync_consent, source_acquisition_consent
from cifereca.core.legacy_source_consent_metadata import (
    _consent_generation_key, _source_id, legacy_source_consent_switch_present,
)
from cifereca.core.legacy_source_identifier import legacy_source_id
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope


@pytest.mark.parametrize("provider,account,prefix,epoch", [
    ("numilog", "borrower", "sync.source.numilog.materialization.consent.v1.",
     "sync.source.numilog.materialization.epoch.v1."),
    ("bnf-pret", "reader", "sync.source.bnf_auto.", None),
    ("kobo", "reader", "sync.source.materialization.on_start.", None),
])
def test_historical_epoch_names_remain_byte_stable(provider, account, prefix, epoch):
    scope = ProviderRightsScope("alice", provider, "card", account)
    sid = legacy_source_id("alice", provider, "card", account)
    assert _source_id(scope) == sid
    assert _consent_generation_key(scope) == (
        epoch + sid if epoch else prefix + sid + ".revision"
    )


def test_unknown_provider_has_no_historical_epoch_or_auto_consent():
    scope = ProviderRightsScope("alice", "future-provider", "card", "reader")
    assert _consent_generation_key(scope) is None
    assert legacy_source_consent_switch_present(
        type("Store", (), {"metadata_get": lambda *_: pytest.fail("unexpected read")})(),
        scope,
    ) is False


@pytest.mark.parametrize("provider,prefix", [
    ("numilog", "sync.source.numilog.materialization.consent.v1."),
    ("bnf-pret", "sync.source.bnf_auto."),
    ("kobo", "sync.source.materialization.on_start."),
])
def test_switch_presence_is_lookup_only_not_a_grant(provider, prefix):
    scope = ProviderRightsScope("alice", provider, "card", "reader")
    present = prefix + _source_id(scope)
    seen = []
    class Storage:
        def metadata_get(self, key):
            seen.append(key)
            return "malformed-or-off"
    assert legacy_source_consent_switch_present(Storage(), scope) is True
    assert seen == [present]


def test_cas_and_compatibility_toggle_do_not_import_consent_decision_bridge():
    for module, method in (
        (source_sync_consent, "set_inventory_source_enabled"),
        (source_acquisition_consent, "legacy_inventory_enablement_toggle"),
    ):
        source = inspect.getsource(getattr(module, method))
        tree = ast.parse(source)
        assert not [
            node for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "source_auto_consent_bridge"
        ]
