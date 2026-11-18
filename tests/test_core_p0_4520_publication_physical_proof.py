"""#4520: physical proofs remain valid at the canonical SQL boundary."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.core import derived_variant_publication as publication
from cifereca.plugin_api import Conflict, TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


@pytest.mark.parametrize("change", ["parent", "output", "symlink", "end_callback"])
def test_changed_physical_proof_never_commits_variant(tmp_path, monkeypatch, change):
    service, source, pid = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    broker = ArtifactExecutionBrokerImpl(service)
    original = publication.publish_derived_variant
    observed = {}

    def intercepted(*args, **kwargs):
        parent = kwargs["parent_path"]
        output = kwargs["published_path"]
        observed["output"] = output
        if change == "parent":
            parent.write_bytes(b"foreign parent")
        elif change == "output":
            output.write_bytes(b"foreign child")
        elif change == "symlink":
            other = tmp_path / "private.epub"
            other.write_bytes(output.read_bytes())
            output.unlink()
            output.symlink_to(other)
            observed["other"] = other
        else:
            check = kwargs["scope_current"]
            count = 0

            def current(db):
                nonlocal count
                count += 1
                result = check(db)
                if count == 2:
                    output.write_bytes(b"foreign child")
                return result

            kwargs["scope_current"] = current
        return original(*args, **kwargs)

    monkeypatch.setattr(publication, "publish_derived_variant", intercepted)
    try:
        request = TransformRequest(
            replace(source, identity_id="id1", placement_id=pid),
            "optimize", identity_id="id1", placement_id=pid,
        )
        operation = broker.admit_transform(
            request, transformer_plugin_id="document.transformer.test",
        )
        with pytest.raises(Conflict) as caught:
            operation(Control())
        assert caught.value.code == "derived_variant_physical_proof_changed"
        assert observed
        assert not service.storage.artifact_variants(source_artifact_id=source.artifact_id)
        assert len(service.storage.placements()) == 1
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT count(*) FROM origin_links WHERE origin_kind='derived'").fetchone() == (0,)
        if change == "symlink":
            assert observed["other"].read_bytes() == b"book-data-optimized"
        elif change == "parent":
            assert (tmp_path / "library" / "Book.epub").read_bytes() == b"foreign parent"
    finally:
        service.storage.close()
