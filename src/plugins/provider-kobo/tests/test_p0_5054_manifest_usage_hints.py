"""Provider-owned Kobo manifest UX contract; never executed by Core tests."""
from pathlib import Path
from cifereca.plugins.manifest import load_manifest


def test_kobo_usage_hints_do_not_claim_a_second_account_or_purchase():
    manifest = load_manifest(Path(__file__).resolve().parents[1] / "cifereca-plugin.json")
    rows = {item.id: item.ui_usage_hint for item in manifest.plugins}
    private = rows["provider.kobo"]
    assert "aucun second compte" in private and "Mes achats" in private
    assert "aucun droit d’achat" in rows["provider.kobo.catalog"]
    for value in rows.values():
        assert len(value) <= 384
        assert "https://" not in value
        assert "credential_id" not in value
