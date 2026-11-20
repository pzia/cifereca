"""#3724: every shipped provider.library manifest has a documentary audit row."""
import json
import re
from collections import Counter
from pathlib import Path


def test_notice_producer_matrix_covers_shipped_provider_manifests():
    root = Path(__file__).resolve().parents[1]
    providers = []
    for manifest in sorted((root / "src" / "plugins").glob("*/cifereca-plugin.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        providers.extend(
            plugin["id"] for plugin in data["plugins"]
            if plugin["type"] == "provider.library"
        )
    assert providers, "manifest inventory unavailable"
    matrix = (root / "docs" / "PLUGINS-P0-3724-NOTICE-PRODUCER-MATRIX.md").read_text(
        encoding="utf-8",
    )
    rows = re.findall(r"^\| `(provider\.[a-z0-9.-]+)` \|", matrix, flags=re.M)
    assert Counter(rows) == Counter(providers)
    assert len(rows) == len(set(rows))
