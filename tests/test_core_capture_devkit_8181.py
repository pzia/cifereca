from hashlib import sha256
import json
from pathlib import Path

from cifereca.plugin_api.capture_vault import core_contract_kit


def test_core_kit_independent_from_browser_recorder():
    kit = core_contract_kit()
    manifest = json.loads(kit["plugin-dev-kit/manifest.json"])
    assert manifest["files"]
    for row in manifest["files"]:
        assert sha256(kit[row["path"]]).hexdigest() == row["sha256"]
    root = Path(__file__).resolve().parents[1]
    assert kit["plugin-dev-kit/PROVIDER-DEVELOPMENT.md"] == (
        root / "docs/PROVIDER-DEVELOPMENT.md"
    ).read_bytes()
