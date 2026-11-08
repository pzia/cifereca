"""P0 #5083: closed, versioned plugin UI hint is read-only and fail-closed."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from cifereca.plugins.manifest import ManifestError, parse_manifest


_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "src/plugins/provider-kobo/cifereca-plugin.json"
)


def _manifest():
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_real_package_hints_are_read_only_descriptor_text():
    parsed = parse_manifest(_manifest())
    hints = {plugin.id: plugin.ui_usage_hint for plugin in parsed.plugins}
    assert "aucun second compte" in hints["provider.kobo"]
    assert "aucun droit d’achat" in hints["provider.kobo.catalog"]
    assert len(hints) == 2
    # No extra plugin, provider route, login or entitlement was created.
    assert len(parsed.plugins) == len(_manifest()["plugins"])


def test_legacy_manifest_has_no_forced_brand_hint():
    source = _manifest()
    for row in source["plugins"]:
        row.pop("ui_contribution", None)
    parsed = parse_manifest(source)
    assert all(plugin.ui_usage_hint == "" for plugin in parsed.plugins)


@pytest.mark.parametrize("untrusted", [
    [],
    "unsafe string",
    {},
    {"schema": "future/2", "usage_hint": "Service technique"},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": "ok", "route": "kobo"},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": ""},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": "x" * 385},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": " padded "},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": "a\nPRIVATE_TOKEN"},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": "https://private.invalid"},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": "Bearer PRIVATE_SECRET"},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": "token=PRIVATE_SECRET"},
    {"schema": "cifereca.plugin.ui/1", "usage_hint": 123},
])
def test_invalid_or_sensitive_ui_contribution_is_refused(untrusted):
    source = copy.deepcopy(_manifest())
    source["plugins"][0]["ui_contribution"] = untrusted
    with pytest.raises(ManifestError):
        parse_manifest(source)
