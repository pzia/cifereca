"""Provider-owned Numilog manifest UX contract; not Core fixture data."""
from pathlib import Path
from cifereca.plugins.manifest import load_manifest


def test_numilog_private_and_public_hints_remain_distinct_and_non_authorizing():
    manifest = load_manifest(Path(__file__).resolve().parents[1] / "cifereca-plugin.json")
    rows = {item.id: item.ui_usage_hint for item in manifest.plugins}
    private = rows["provider.numilog"]
    public = rows["provider.numilog.catalog"]
    assert "Mes achats" in private and "source optionnelle distincte" in private
    assert "sans compte" in public and "sans téléchargement" in public
    assert "indépendante" in public
    for value in rows.values():
        assert len(value) <= 384
        assert "https://" not in value
        assert "credential_id" not in value
