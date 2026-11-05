"""#2854: generic storage.target can never bypass USB copy admission."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.artifact_runtime import (
    ArtifactExecutionBrokerImpl, ArtifactRoutingBrokerImpl,
)
from cifereca.plugin_api import (
    ArtifactRoutePlan, ArtifactRouteRequest, ArtifactRouteTarget,
    StorageTarget, UnsupportedOperation, NotFound,
)
from tests.test_core041_artifact_authority import FakeManager, make_service


class UsbTargetPlugin:
    def __init__(self, target: StorageTarget):
        self.target = target
        self.import_calls = 0

    def configured_targets(self):
        return (self.target,)

    def matches(self, target):
        return True

    def import_artifact(self, *args, **kwargs):
        self.import_calls += 1
        pytest.fail("unsafe generic importer must never be invoked for USB")


@pytest.mark.parametrize("kind,metadata", [
    ("usb", {}),
    ("USB", {}),
    ("removable", {}),
    ("usb-mass-storage", {}),
    ("filesystem", {"transport": "usb"}),
    ("filesystem", {"usb_fingerprint": "uuid-alice-001"}),
])
def test_recognized_usb_target_is_hidden_and_unexecutable(
    tmp_path: Path, kind: str, metadata: dict[str, object],
):
    service, artifact, _placement = make_service(tmp_path)
    mount = tmp_path / "usb-standin"
    target = StorageTarget(
        "reader", mount, "Reader", kind=kind, metadata=metadata,
    )
    plugin = UsbTargetPlugin(target)
    service.plugin_manager = FakeManager(target=plugin)
    broker = ArtifactRoutingBrokerImpl(service, ArtifactExecutionBrokerImpl(service))
    request = ArtifactRouteRequest(artifact, identity_id="id1", target_id="reader")
    original = service.storage.placements()
    assert broker.targets(request) == ()
    with pytest.raises(NotFound):
        broker.plan(request)

    # A stale/forged plan bypasses discovery but must *still* fail before
    # input lease, plugin import, target writes or a success receipt.
    plan = ArtifactRoutePlan(
        request=request,
        target=ArtifactRouteTarget(
            "storage.test", target, 20, 8, (),
        ),
        filename="Book.epub",
    )
    with pytest.raises(UnsupportedOperation) as exc:
        broker.transfer(plan)
    assert getattr(exc.value, "code", None) == (
        "usb_core_authorization_required"
    )
    assert plugin.import_calls == 0
    assert not mount.exists()
    assert service.storage.placements() == original


def test_non_usb_storage_target_still_routes_normally(tmp_path: Path):
    service, artifact, _placement = make_service(tmp_path)
    target = StorageTarget(
        "network-share", tmp_path / "ordinary-storage", "Ordinary storage",
        kind="filesystem", metadata={"transport": "filesystem"},
    )
    plugin = UsbTargetPlugin(target)
    service.plugin_manager = FakeManager(target=plugin)
    broker = ArtifactRoutingBrokerImpl(service, ArtifactExecutionBrokerImpl(service))
    request = ArtifactRouteRequest(artifact, identity_id="id1")
    targets = broker.targets(request)
    assert len(targets) == 1
    assert targets[0].target == target
    assert plugin.import_calls == 0


def test_even_false_or_corrupt_usb_fingerprint_is_not_positive_usb_clearance(
    tmp_path: Path,
):
    service, artifact, _placement = make_service(tmp_path)
    target = StorageTarget(
        "reader", tmp_path / "usb-standin", "Reader",
        kind="filesystem", metadata={"usb_fingerprint": None},
    )
    plugin = UsbTargetPlugin(target)
    service.plugin_manager = FakeManager(target=plugin)
    broker = ArtifactRoutingBrokerImpl(service, ArtifactExecutionBrokerImpl(service))
    assert broker.targets(ArtifactRouteRequest(artifact)) == ()
