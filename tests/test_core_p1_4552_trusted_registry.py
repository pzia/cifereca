from dataclasses import replace
from hashlib import sha256
import json

import pytest

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.core.notice_consolidator_trusted_registry import TrustedConsolidatorRegistry
from cifereca.core.notice_descriptive_selection_store import DescriptiveSelectionPin
from cifereca.core.notice_consolidator_selection import ExplicitSelection
from cifereca.core.notice_canonical_text_schema import canonical_text_schema_digest
from tests.test_core_p1_4552_consolidator_archive import payload, qualify
from tests.test_core_p1_4552_consolidator_manifest import MEMBER, SOURCE, row


def trusted(data):
    return TrustedConsolidatorRegistry(((data, sha256(data).hexdigest()),))


def test_trusted_pin_requires_exact_active_archive_code_and_schema(tmp_path):
    data = payload()
    candidate, = qualify(data).candidates
    pin = DescriptiveSelectionPin(
        1, ExplicitSelection("age", "fr:editions", "plugin.age.alpha", "1.0.0",
                             "schema.age.v1"),
        candidate.installed.source_sha256, canonical_text_schema_digest(candidate.schema),
    )
    manager = PluginManager(package_store=PackageStore(tmp_path / "packages"),
                            state_store=PluginStateStore(tmp_path / "state.json"))
    archive = tmp_path / "archive.zip"
    archive.write_bytes(data)
    registry = trusted(data)
    try:
        with registry.hold_selected(manager, pin) as active:
            assert active is None
        installed = manager.installer.install(archive)
        manager.refresh()
        with registry.hold_selected(manager, pin) as active:
            assert active == candidate
        with TrustedConsolidatorRegistry(()).hold_selected(manager, pin) as active:
            assert active is None
        for invalid in (None, replace(pin, schema_sha256=None),
                        DescriptiveSelectionPin(2, None, None)):
            with registry.hold_selected(manager, invalid) as active:
                assert active is None
        for drift in (replace(pin, schema_sha256="0"*64),
                      replace(pin, installed_source_sha256="0"*64)):
            with pytest.raises(ValueError, match="descriptive_selection"):
                with registry.hold_selected(manager, drift):
                    pytest.fail("drift admitted")
        manager.state.set_enabled("plugin.age.alpha", False)
        with registry.hold_selected(manager, pin) as active:
            assert active is None
        manager.state.set_enabled("plugin.age.alpha", True)
        manager.refresh()
        (installed.root / MEMBER).write_bytes(SOURCE + b"# drift")
        with pytest.raises(ValueError, match="active_package_changed"):
            with registry.hold_selected(manager, pin):
                pytest.fail("modified executable admitted")
    finally:
        manager.close()


def test_independent_archive_digest_and_ambiguity():
    data = payload()
    with pytest.raises(ValueError, match="digest_mismatch"):
        TrustedConsolidatorRegistry(((data, "0"*64),))
    exact = (data, sha256(data).hexdigest())
    with pytest.raises(ValueError, match="ambiguous"):
        TrustedConsolidatorRegistry((exact, exact))
    other = payload(manifest=json.dumps(dict(
        manifest=1, package=dict(id="package.other", version="1.0.0"),
        plugins=[dict(id="plugin.age.alpha", type="meta.bundle",
                      version="1.0.0", capabilities=["metadata.consolidator"])],
        notice_text_consolidators=[row()],
    )))
    with pytest.raises(ValueError, match="ambiguous"):
        TrustedConsolidatorRegistry((exact, (other, sha256(other).hexdigest())))
    with pytest.raises(ValueError, match="invalid_trusted"):
        TrustedConsolidatorRegistry((exact,) * 17)
