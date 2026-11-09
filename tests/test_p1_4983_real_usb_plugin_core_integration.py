"""#4983: execute the real USB storage.target with Core publication on a fake volume."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from cifereca.core.device_usb_copy_supervisor import execute_usb_copy
from tests.test_p1_2854_usb_copy_supervisor import copy


def test_real_storage_usb_plugin_copy_and_observe_verified_bytes(
    copy, monkeypatch,
):
    args, managed, _observations, resolved = copy
    plugin_dir = (Path(__file__).resolve().parents[1] /
                  "src/plugins/storage-usb")
    monkeypatch.syspath_prepend(str(plugin_dir))
    from cifereca_storage_usb import PLUGIN

    real_service = PLUGIN.activate(None)
    resolved.service = real_service
    result = execute_usb_copy(**args)
    assert result.status == "completed", result.reason

    physical = managed / (args["consent"].sha256 + ".epub")
    assert physical.read_bytes() == args["payload"]
    rows = real_service.observe_usb_files(
        managed.parent, ("Books/" + physical.name,),
    )
    assert len(rows) == 1
    assert rows[0].status == "present"
    assert rows[0].sha256 == sha256(args["payload"]).hexdigest()
    assert args["journal"].get(args["consent"].operation_id).phase == "completed"
