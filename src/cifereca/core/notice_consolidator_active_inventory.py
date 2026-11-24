"""Trusted Core lifecycle owner: qualify current installed consolidators.

Run on the serialized PluginManager lifecycle owner, never from provider code.
The managed package mutation lease covers the installed snapshot. The
independent expected archive digest is mandatory. Returned candidates are
a snapshot, not a durable authority or permission to execute later.
"""
from contextlib import contextmanager
from io import BytesIO
import os
import stat
import zipfile

from ..plugins.manager import PluginManager
from ..plugins.manifest import MANIFEST_FILENAME
from .plugin_package_mutation_lock import package_mutation_lease
from .notice_consolidator_archive import (
    qualify_consolidator_archive, _MAX_MANIFEST_BYTES,
)
from .notice_consolidator_manifest import bind_verified_package_consolidators
from .notice_consolidator_runner import MAX_SCRIPT_BYTES


def _read_at(root, parts, limit):
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                raise ValueError("consolidator_installed_member_invalid")
            data = stream.read(limit + 1)
            if len(data) != info.st_size:
                raise ValueError("consolidator_installed_member_invalid")
            return data
    finally:
        os.close(directory)


@contextmanager
def hold_active_consolidator_inventory(manager, payload, *, expected_sha256):
    """Yield enabled candidates while managed package mutations are fenced.

    No refresh, activation, import, filesystem writes or implicit trust.
    A missing/disabled owner is excluded. Inconsistent bytes or duplicate
    owners across active packages fail closed rather than selecting by order.
    Caller serializes manager lifecycle and must not retain this authority.
    """
    if type(manager) is not PluginManager:
        raise ValueError("invalid_consolidator_manager")
    verified = qualify_consolidator_archive(payload, expected_sha256=expected_sha256)
    store = manager.packages
    with package_mutation_lease(store.root):
        if store.active_version(verified.package_id) != verified.package_version:
            yield ()
            return
        package = store.load(verified.package_id)
        if (package.sha256 != verified.archive_sha256
                or package.manifest.package.id != verified.package_id
                or package.manifest.package.version != verified.package_version):
            raise ValueError("consolidator_active_package_changed")
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                if _read_at(package.root, (MANIFEST_FILENAME,), _MAX_MANIFEST_BYTES) != archive.read(MANIFEST_FILENAME):
                    raise ValueError("consolidator_active_package_changed")
            members = {}
            for row in package.manifest.raw.get("notice_text_consolidators", []):
                member = row["source_member"]
                # The exact manifest bytes were authenticated above.
                members[member] = _read_at(package.root, tuple(member.split("/")), MAX_SCRIPT_BYTES)
            candidates = bind_verified_package_consolidators(package.manifest, verified_members=members)
            if candidates != verified.candidates:
                raise ValueError("consolidator_active_package_changed")
        except (OSError, KeyError, TypeError, ValueError):
            raise ValueError("consolidator_active_package_changed") from None
        owners = {}
        for active in store.active_packages():
            for descriptor in active.manifest.plugins:
                owners[descriptor.id] = owners.get(descriptor.id, 0) + 1
        result = []
        for candidate in candidates:
            plugin_id = candidate.declaration.plugin_id
            if owners.get(plugin_id) != 1:
                raise ValueError("ambiguous_consolidator_active_owner")
            record = manager._records.get(plugin_id)
            if record is None or not record.enabled or record.state != "ready":
                continue
            if (record.package.root != package.root or record.package.sha256 != package.sha256
                    or record.descriptor not in package.manifest.plugins):
                raise ValueError("consolidator_active_package_changed")
            if manager.state.enabled_override(plugin_id) is False or manager._disable_plugins:
                continue
            result.append(candidate)
        yield tuple(result)
