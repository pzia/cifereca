"""P1 #2854: per-reader format declarations are revocable, not USB rights."""
from __future__ import annotations

from copy import deepcopy
import pytest

from cifereca.core.device_usb_reader_formats import declared_usb_reader_formats
from cifereca.core.device_usb_preview import UsbLocalArtifact
from cifereca.core.device_usb_variant_review import review_usb_variant
from cifereca.storage import Storage
from tests.test_p1_2854_usb_binding_declaration import _db, _register


SHA = "a" * 64
FP = "uuid-001-alpha"


def set_formats(db, formats=("epub",), *, identity="alice",
                device="reader-a", fingerprint=FP):
    return db.device_usb_reader_formats_set(
        identity_id=identity, device_id=device,
        usb_fingerprint=fingerprint, supported_formats=formats,
    )


def read_formats(binding, *, identity="alice", device="reader-a", fp=FP):
    return declared_usb_reader_formats(
        identity_id=identity, device_id=device,
        usb_fingerprint=fp, binding=binding,
    )


def test_persisted_formats_are_scoped_and_revocable(tmp_path):
    db = _db(tmp_path)
    _register(db)
    before = db.device_binding_get("reader-a")
    assert read_formats(before) is None
    assert set_formats(db, ("epub", "kepub", "fxl.kepub", "pdf")) == "reader-a"
    saved = db.device_binding_get("reader-a")
    assert saved["metadata"] == {
        "usb_fingerprint": FP,
        "reader_formats": ["epub", "kepub", "fxl.kepub", "pdf"],
    }
    assert saved["enabled"] is False
    assert saved["target_directory"] == before["target_directory"]
    assert db.device_placements() == []
    reopened = Storage(tmp_path / "library.sqlite3")
    declaration = read_formats(reopened.device_binding_get("reader-a"))
    assert declaration.supported_formats == ("epub", "kepub", "fxl.kepub", "pdf")
    assert declaration.identity_id == "alice"
    assert declaration.usb_fingerprint == FP
    set_formats(db, ())
    assert read_formats(db.device_binding_get("reader-a")) is None
    assert db.device_binding_get("reader-a")["metadata"] == {"usb_fingerprint": FP}


def test_formats_never_touch_device_intents_or_existing_metadata(tmp_path):
    db = _db(tmp_path)
    _register(db, enabled=True)
    row = db.device_binding_get("reader-a")
    db.device_binding_put({**row, "metadata": {
        **row["metadata"], "custom_extension": {"version": 2},
    }})
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": "artifact",
        "desired_state": "keep", "observed_state": "present",
        "last_observed_at": 1000, "target_relative_path": "Books/Cifereca/a.epub",
        "metadata": {"intent_identity_id": "alice", "receipt": "old"},
    })
    intents = deepcopy(db.device_placements())
    set_formats(db, ("kepub", "epub"))
    before = deepcopy(db.device_binding_get("reader-a"))
    set_formats(db, ("kepub", "epub"))
    assert db.device_binding_get("reader-a") == before
    assert before["metadata"]["custom_extension"] == {"version": 2}
    assert before["enabled"] is True
    _register(db, label="New label", enabled=False)
    assert db.device_binding_get("reader-a")["metadata"]["reader_formats"] == [
        "kepub", "epub",
    ]
    assert db.device_placements() == intents


@pytest.mark.parametrize("formats", [
    None, False, [], ["epub"], "epub", ("EPUB",),
    ("acsm",), ("epub", "epub"), ("epub", "lcp"),
    ("epub", None), (None,), (1,), (True,),
    ("epub", "pdf", "kepub", "fxl.kepub", "other"),
])
def test_invalid_formats_are_rejected_without_sqlite_mutation(tmp_path, formats):
    db = _db(tmp_path)
    _register(db)
    before = db.device_bindings()
    with pytest.raises(ValueError):
        set_formats(db, formats)
    assert db.device_bindings() == before


@pytest.mark.parametrize("identity,device,fp", [
    ("bob", "reader-a", FP), ("alice", "reader-b", FP),
    ("alice", "reader-a", "uuid-different"),
    ("alice", "reader-a", "UUID-001-ALPHA"),
    ("alice", "reader-a", "../bad"), ("alice", "reader-a", ""),
])
def test_foreign_identity_or_changed_volume_cannot_save(tmp_path, identity, device, fp):
    db = _db(tmp_path)
    _register(db)
    before = db.device_bindings()
    with pytest.raises(ValueError):
        set_formats(db, identity=identity, device=device, fingerprint=fp)
    assert db.device_bindings() == before


@pytest.mark.parametrize("changes", [
    {"identity_id": "bob"}, {"device_id": "reader-b"},
    {"metadata": {"usb_fingerprint": "wrong", "reader_formats": ["epub"]}},
    {"metadata": {"usb_fingerprint": FP, "reader_formats": []}},
    {"metadata": {"usb_fingerprint": FP, "reader_formats": ["acsm"]}},
    {"metadata": {"usb_fingerprint": FP, "reader_formats": ["epub", "epub"]}},
    {"metadata": {"usb_fingerprint": FP, "reader_formats": ("epub",)}},
    {"metadata": {"usb_fingerprint": FP, "reader_formats": True}},
])
def test_corrupt_reader_profile_never_generates_a_supported_format(changes):
    row = {
        "identity_id": "alice", "device_id": "reader-a",
        "metadata": {"usb_fingerprint": FP, "reader_formats": ["epub"]},
    }
    assert read_formats({**row, **changes}) is None


def test_formats_activated_still_cannot_grant_copy_permission(tmp_path):
    db = _db(tmp_path)
    _register(db, enabled=True)
    set_formats(db, ("epub",))
    binding = db.device_binding_get("reader-a")
    reader = read_formats(binding)
    assert reader is not None
    template = dict(
        identity_id="alice", binding=binding,
        local=UsbLocalArtifact("alice", SHA, True),
        target_relative_path="Books/Cifereca/book.epub", reader=reader,
    )
    artifact = {
        "artifact_id": "sha256:" + SHA, "sha256": SHA,
        "size": 100, "kind": "file", "media_format": "epub",
        "metadata": {"protected": False, "post_processed": True},
    }
    approved = review_usb_variant(
        **template, artifact=artifact, selected_format="epub",
    )
    assert approved.status == "review"
    assert approved.reason == "drm_and_transfer_rights_unverified"
    assert approved.executable is False

    # A declared EPUB does not establish PDF support or any right to copy.
    denied = review_usb_variant(
        identity_id="alice", binding=binding,
        local=UsbLocalArtifact("alice", SHA, True), reader=reader,
        artifact={**artifact, "media_format": "pdf"},
        selected_format="pdf", target_relative_path="Books/Cifereca/book.pdf",
    )
    assert denied.reason == "reader_format_unsupported"
    assert denied.executable is False
