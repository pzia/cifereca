"""Real Linux directory identity and transactional pseudo-reader binding."""
from pathlib import Path
import pytest
from cifereca.identities import Identity
from cifereca.storage import Storage
from cifereca.core.device_usb_mount import observe_linux_usb_mount


def test_local_directory_binding_is_exclusive_and_replacement_refused(tmp_path):
    db = Storage(tmp_path / "library.sqlite3")
    for owner in ("alice", "bob"):
        db.identity_put(Identity(owner, owner))
    root = tmp_path / "reader"
    root.mkdir()
    args = dict(identity_id="alice", device_id="local-a", label="Local",
                root=root, enabled=True)
    assert db.device_local_reader_declare(**args) == "local-a"
    binding = db.device_binding_get("local-a")
    proof = observe_linux_usb_mount(identity_id="alice", binding=binding, mountpoint=root)
    assert proof is not None
    assert proof.filesystem.startswith("local:")
    assert observe_linux_usb_mount(identity_id="bob", binding=binding, mountpoint=root) is None
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**(args | {"identity_id": "bob", "device_id": "local-b"}))
    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**(args | {"root": nested, "device_id": "local-c"}))
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**(args | {"root": alias, "device_id": "local-d"}))
    root.rename(tmp_path / "original")
    root.mkdir()
    assert observe_linux_usb_mount(identity_id="alice", binding=binding, mountpoint=root) is None
    with pytest.raises(ValueError):
        db.device_local_reader_declare(**args)
