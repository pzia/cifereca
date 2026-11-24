"""Inert text consolidator manifest contract for a verified package inventory.

This port does not import entrypoints, read files, authenticate packages or
activate a service. The caller owns verified manifest and member bytes.
No source observation or plugin callback may supply either argument.
"""
from dataclasses import dataclass
from hashlib import sha256
import re

from .notice_consolidator_selection import ConsolidatorDeclaration, _identifier
from .notice_canonical_text_schema import CanonicalTextSchema, _valid_schema
from .notice_consolidator_runner import InstalledTextConsolidator, MAX_SCRIPT_BYTES

_PATH = re.compile(r"consolidators/[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}\.py\Z")
_FIELDS = {"plugin_id", "version", "canonical_type", "scope", "schema_id",
           "source_member", "source_sha256", "max_bytes", "inherited_allowed",
           "allowed_values"}


@dataclass(frozen=True, slots=True)
class ManifestTextConsolidator:
    declaration: ConsolidatorDeclaration
    schema: CanonicalTextSchema
    installed: InstalledTextConsolidator


def decode_consolidator_manifest(rows, *, verified_members):
    """Decode bounded V1 declarations with exact reviewed member hashes.

    verified_members maps canonical package-relative member names to bytes
    from the same authenticated package snapshot. It is not a filesystem
    resolver. Unknown fields, duplicates and missing content fail closed.
    """
    if (type(rows) not in (tuple, list) or len(rows) > 128
            or type(verified_members) is not dict or len(verified_members) > 128
            or any(type(key) is not str or type(value) is not bytes
                   for key, value in verified_members.items())):
        raise ValueError("invalid_consolidator_manifest")
    result = []
    identities = set()
    for row in rows:
        if (type(row) is not dict or any(type(key) is not str for key in row)
                or set(row) != _FIELDS):
            raise ValueError("invalid_consolidator_manifest")
        kind, scope, plugin, version, schema_id = (
            row["canonical_type"], row["scope"], row["plugin_id"],
            row["version"], row["schema_id"],
        )
        if (not all(_identifier(value) for value in (kind, plugin, version, schema_id))
                or not _identifier(scope, max_len=160)):
            raise ValueError("invalid_consolidator_manifest")
        identity = (kind, scope, plugin, version)
        if identity in identities:
            raise ValueError("ambiguous_consolidator_manifest")
        identities.add(identity)
        member, digest = row["source_member"], row["source_sha256"]
        if (type(member) is not str or _PATH.fullmatch(member) is None
                or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            raise ValueError("invalid_consolidator_manifest_member")
        data = verified_members.get(member)
        if (data is None or not 0 < len(data) <= MAX_SCRIPT_BYTES
                or sha256(data).hexdigest() != digest):
            raise ValueError("consolidator_manifest_member_mismatch")
        try:
            source = data.decode("utf-8")
        except UnicodeError:
            raise ValueError("consolidator_manifest_member_mismatch") from None
        allowed = row["allowed_values"]
        if type(allowed) is not list or len(allowed) > 128:
            raise ValueError("invalid_consolidator_manifest_schema")
        schema = CanonicalTextSchema(
            kind, scope, plugin, version, schema_id, row["max_bytes"],
            row["inherited_allowed"], tuple(allowed),
        )
        if not _valid_schema(schema):
            raise ValueError("invalid_consolidator_manifest_schema")
        result.append(ManifestTextConsolidator(
            ConsolidatorDeclaration(plugin, kind, scope, version, schema_id),
            schema, InstalledTextConsolidator(
                kind, scope, plugin, version, schema_id, source, digest,
            ),
        ))
    return tuple(result)
