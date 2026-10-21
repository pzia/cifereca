"""P1 #2665: deterministic, scoped versions snapshot for both Diagnostics pages."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cifereca.plugin_api.version_inventory import version_inventory


def _row(plugin_id, name, version, *, enabled=True, state="ready",
         package_version="0.1.3", expected="0.1.4", **extra):
    return {
        "plugin_id": plugin_id,
        "display_name": name,
        "version": version,
        "state": state,
        "enabled": enabled,
        "package_version": package_version,
        "expected_package_version": expected,
        **extra,
    }


def _snapshot():
    return version_inventory({"version": "2.4.1", "token": "STATUS_SECRET"}, [
        _row("provider.good", "Source saine", "3.0.0", state="ready"),
        _row("browser.recorder", "Browser Recorder", "0.1.3", state="blocked"),
        _row("plugin.disabled", "Extension désactivée", "2.1", enabled=False,
             state="disabled", package_version="2.1", expected="2.1"),
    ])


def test_core_always_first_even_when_everything_else_healthy():
    snapshot = version_inventory({"version": "2026.10"}, [
        _row("plugin.ready", "Module prêt", "1.0", expected="1.0",
             package_version="1.0"),
    ])
    assert snapshot.text.startswith("Core Cifereca · v2026.10\n")
    assert len(snapshot.plugins) == 1
    assert "Module prêt [plugin.ready]" in snapshot.text


def test_versions_are_manifest_versions_not_package_or_expected():
    snapshot = _snapshot()
    row = next(row for row in snapshot.plugins if row.plugin_id == "browser.recorder")
    assert row.version == "0.1.3"
    assert "Extension : 0.1.3" in row.details
    assert "Paquet actif : 0.1.3" in row.details
    assert "Paquet attendu : 0.1.4" in row.details
    assert "Écart paquet actif/attendu" in row.details
    assert "Bloqué · activé : oui" in row.details


def test_disabled_ready_and_blocked_are_all_listed_with_attention_first():
    snapshot = _snapshot()
    assert [row.plugin_id for row in snapshot.plugins] == [
        "browser.recorder", "plugin.disabled", "provider.good",
    ]
    assert "Désactivé · activé : non" in snapshot.text
    assert "Prêt · activé : oui" in snapshot.text


def test_output_order_independent_of_unrelated_broker_field_order():
    rows = [
        _row("b", "B", "2"),
        _row("a", "A", "1"),
    ]
    a = version_inventory({"version": "1"}, rows)
    b = version_inventory({"version": "1"}, [dict(reversed(list(row.items()))) for row in rows[::-1]])
    assert a.text == b.text


def test_no_secrets_from_unrelated_broker_diagnostic_or_support_fields():
    sentinel = "SENSITIVE_PRIVATE_2665"
    dirty = _row("plugin.clean", "Extension normale", "3.1",
                 token=sentinel, account_id=sentinel,
                 path="/home/test/" + sentinel,
                 support_report={"stderr": sentinel},
                 raw_credential=sentinel)
    snapshot = version_inventory({
        "version": "8.0.0",
        "packages": [{"source_account": sentinel}],
        "plugins": [{"title": sentinel}],
        "support": sentinel,
    }, [dirty])
    assert sentinel not in snapshot.text
    assert "/home/" not in snapshot.text
    assert "Extension normale" in snapshot.text


@pytest.mark.parametrize("field,unsafe", [
    ("plugin_id", "https://sample.invalid/a"),
    ("plugin_id", "id-with@email"),
    ("display_name", "secret=PRIVATE_TOKEN"),
    ("display_name", "/home/person/a"),
    ("version", "0.1.3 TOKEN_X"),
    ("package_version", "../private"),
    ("expected_package_version", "Bearer another-secret"),
    ("state", "token=SECRET"),
])
def test_untrusted_manifest_field_never_reaches_copied_text(field, unsafe):
    row = _row("plugin.clean", "Plugin", "1.2")
    row[field] = unsafe
    snapshot = version_inventory({"version": "1.0"}, [row])
    assert unsafe not in snapshot.text
    assert "Bearer" not in snapshot.text


def test_unavailable_broker_values_fail_closed():
    snapshot = version_inventory({"version": "/home/me/secret"}, [
        {"plugin_id": "plugin.unknown", "version": {"token": "raw"},
         "state": "custom-code", "enabled": "yes"},
        {"plugin_id": "token=SECRET", "version": "0.9"},
        None,
    ])
    assert snapshot.core_version == "inconnue"
    assert len(snapshot.plugins) == 1
    assert "Extension : inconnue" in snapshot.text
    assert "Inconnu · activé : inconnu" in snapshot.text
    assert "token=SECRET" not in snapshot.text


def test_projection_is_frozen_and_unaffected_by_mutating_broker_source():
    raw = _row("plugin.clean", "Module initial", "0.1.3")
    snapshot = version_inventory({"version": "1.0"}, [raw])
    text = snapshot.text
    raw["version"] = "9.9.9"
    raw["display_name"] = "Nom changé"
    assert snapshot.text == text
    with pytest.raises(FrozenInstanceError):
        snapshot.core_version = "mutable"


def test_bounded_report_marks_truncation_without_reading_unbounded_tail():
    def entries():
        for i in range(400):
            yield _row(f"module.{i:03}", "Module", "1.0")
    snapshot = version_inventory({"version": "1"}, entries())
    assert len(snapshot.plugins) == 300
    assert snapshot.limited
    assert "limité à 300" in snapshot.text


def test_pages_share_exact_projector_and_copy_only_displayed_snapshot():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    fallback = (root / "src/cifereca/builtin_ui/system_page.py").read_text()
    external = (root / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py").read_text()
    for source in (fallback, external):
        assert "from cifereca.plugin_api.version_inventory import version_inventory" in source
        assert "Copier les versions" in source
        assert "versions_snapshot[0]" in source
        assert "get_clipboard().set_content(provider)" in source
        assert "Versions copiées" in source
    assert "version_inventory(data, broker.inventory())" in fallback
    assert "version_inventory(status, inventory)" in external
