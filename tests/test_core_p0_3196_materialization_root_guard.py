"""#3196: canonical preparation must not reach retired W4 path machinery."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.core.local_profile_root_guards import (
    ProfileRootRejected, checked_profile_roots,
)


def test_canonical_preparation_uses_provider_neutral_root_guard():
    source = (
        Path(__file__).resolve().parents[1] /
        "src/cifereca/core/source_materialization.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "kobo_local_reconciliation" not in imported_modules
    assert "local_profile_root_guards" in imported_modules
    assert "checked_profile_roots" in source


def test_root_guard_refuses_overlapping_profiles_before_scanning():
    class Identity:
        def __init__(self, identity_id, directory_name):
            self.identity_id = identity_id
            self.directory_name = directory_name

    class Service:
        identities = (Identity("alice", "shared"), Identity("bob", "shared"))

        def identity_by_id(self, identity_id):
            return next(
                (row for row in self.identities if row.identity_id == identity_id),
                None,
            )

    invoked = []
    def roots(service, identity_id):
        invoked.append(identity_id)
        return Path("/offline"), Path("/offline/shared")

    import pytest
    with pytest.raises(ProfileRootRejected, match="ambiguous_identity_directory"):
        checked_profile_roots(Service(), "alice", roots_for_identity=roots)
    assert invoked == ["alice"]
