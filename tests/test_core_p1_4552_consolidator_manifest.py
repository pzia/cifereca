from hashlib import sha256

import pytest

from cifereca.core.notice_consolidator_manifest import decode_consolidator_manifest


MEMBER = "consolidators/age.py"
# Deliberately not executable: the decoder must never compile or import it.
SOURCE = b"raise RuntimeError('must not run')\n"


def row(**changes):
    return dict(plugin_id="plugin.age.alpha", version="1.0.0",
                canonical_type="age", scope="fr:editions", schema_id="schema.age.v1",
                source_member=MEMBER, source_sha256=sha256(SOURCE).hexdigest(),
                max_bytes=32, inherited_allowed=True, allowed_values=["13+"],
                **{}) | changes


def test_verified_member_is_decoded_without_execution():
    candidate, = decode_consolidator_manifest([row()], verified_members={MEMBER: SOURCE})
    assert candidate.declaration.plugin_id == "plugin.age.alpha"
    assert candidate.schema.allowed_values == ("13+",)
    assert candidate.installed.source.encode() == SOURCE
    assert candidate.installed.source_sha256 == sha256(SOURCE).hexdigest()


@pytest.mark.parametrize("changes", [
    {"source_member": "../age.py"},
    {"source_member": "/consolidators/age.py"},
    {"source_member": "consolidators/../age.py"},
    {"source_sha256": "0"*64},
    {"max_bytes": True},
    {"inherited_allowed": 1},
    {"allowed_values": ["13+", "13+"]},
    {"allowed_values": ["x"*33]},
    {"plugin_id": "invalid plugin"},
    {"unexpected": True},
])
def test_bad_manifest_fails_closed(changes):
    with pytest.raises(ValueError):
        decode_consolidator_manifest([row(**changes)], verified_members={MEMBER: SOURCE})


def test_missing_modified_and_duplicate_members_are_refused():
    for members in ({}, {MEMBER: SOURCE+b"changed"}):
        with pytest.raises(ValueError, match="member_mismatch"):
            decode_consolidator_manifest([row()], verified_members=members)
    with pytest.raises(ValueError, match="ambiguous"):
        decode_consolidator_manifest([row(), row()], verified_members={MEMBER: SOURCE})


def test_no_manifest_no_implicit_candidate():
    assert decode_consolidator_manifest([], verified_members={MEMBER: SOURCE}) == ()


def test_real_package_manifest_binds_inert_owner():
    from cifereca.plugins.manifest import parse_manifest
    from cifereca.core.notice_consolidator_manifest import bind_verified_package_consolidators

    owner = dict(id="plugin.age.alpha", type="meta.bundle",
                 version="1.0.0", capabilities=["metadata.consolidator"])
    raw = dict(manifest=1, package=dict(id="package.age", version="1.0.0"),
               plugins=[owner], notice_text_consolidators=[row()])
    package = parse_manifest(raw)
    candidate, = bind_verified_package_consolidators(package, verified_members={MEMBER: SOURCE})
    assert candidate.declaration.plugin_id == package.plugins[0].id
    for changes in (
        {"id": "plugin.age.foreign"},
        {"version": "1.0.1"},
        {"capabilities": []},
        {"entrypoint": "unsafe:plugin"},
        {"requires_brokers": ["tasks"]},
        {"type": "provider.library", "entrypoint": "unsafe:plugin"},
    ):
        wrong = parse_manifest({**raw, "plugins": [{**owner, **changes}]})
        with pytest.raises(ValueError, match="owner_mismatch"):
            bind_verified_package_consolidators(wrong, verified_members={MEMBER: SOURCE})
