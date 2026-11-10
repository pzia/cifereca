"""#3196: personal inventory cannot publish Kobo pseudo-rights in Core."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "cifereca"


def test_core_inventory_does_not_dispatch_kobo_entitlement_mirror():
    for path in (ROOT / "services.py", ROOT / "core" / "sync_coordination.py"):
        source = path.read_text(encoding="utf-8")
        assert "mirror_kobo_manifest_rights" not in source
        assert "from .kobo_manifest_rights import" not in source
        assert "from .core.kobo_manifest_rights import" not in source
