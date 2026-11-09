"""#4552: an explicitly chosen, versioned, per-type consolidator ABI.

This is a *pure declaration resolver*, not a plugin loader, privilege
grant, reducer or source writer. The Core caller must independently vet
installed plugins, manifests, signatures and active identity/scope.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

MAX_DECLARATIONS = 128
MAX_ID = 128
MAX_SCOPE = 160


@dataclass(frozen=True, slots=True)
class ConsolidatorDeclaration:
    """A candidate plugin's declared support, never permission to execute."""

    plugin_id: str
    canonical_type: str
    scope: str
    version: str
    schema_id: str


@dataclass(frozen=True, slots=True)
class ExplicitSelection:
    canonical_type: str
    scope: str
    plugin_id: str
    version: str
    schema_id: str


def _identifier(value: Any, *, max_len: int = MAX_ID) -> bool:
    return (type(value) is str and 0 < len(value) <= max_len
            and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/+-]*", value) is not None)


def select_consolidator(
    declarations: object, *, canonical_type: str, scope: str,
    selected_plugin_id: str | None, selected_version: str | None,
) -> ExplicitSelection | None:
    """Require explicit exact plugin+version for each canonical type/scope.

    A missing selection returns None, even when there is one candidate.
    Two matching declarations (including conflicts) are ambiguous and fail.
    There is no fallback, first-match ordering, or cross-scope substitution.
    """
    if (not _identifier(canonical_type) or not _identifier(scope, max_len=MAX_SCOPE)
            or type(declarations) not in (tuple, list)
            or len(declarations) > MAX_DECLARATIONS):
        raise ValueError("invalid_consolidator_registry")
    # Validate the full registry even when no consolidator was selected.
    # An unselected or unrelated malformed declaration must not bypass the
    # Core admission boundary merely because execution would be skipped.
    no_selection = selected_plugin_id is None and selected_version is None
    if not no_selection and (not _identifier(selected_plugin_id)
                             or not _identifier(selected_version)):
        raise ValueError("incomplete_consolidator_selection")
    matched: list[ConsolidatorDeclaration] = []
    registered: set[tuple[str, str, str, str]] = set()
    for row in declarations:
        if type(row) is not ConsolidatorDeclaration:
            raise ValueError("invalid_consolidator_declaration")
        if not all(_identifier(x, max_len=MAX_SCOPE if i == 2 else MAX_ID)
                   for i, x in enumerate((
                       row.plugin_id, row.canonical_type, row.scope,
                       row.version, row.schema_id,
                   ))):
            raise ValueError("invalid_consolidator_declaration")
        identity = (row.canonical_type, row.scope, row.plugin_id, row.version)
        # Even an unselected or unrelated duplicate is an ambiguous registry.
        # Never let a missing selection skip the global declaration check.
        if identity in registered:
            raise ValueError("ambiguous_consolidator_declaration")
        registered.add(identity)
        if (row.canonical_type == canonical_type and row.scope == scope
                and row.plugin_id == selected_plugin_id
                and row.version == selected_version):
            matched.append(row)
    if no_selection:
        return None  # No implicit selection, after full-registry validation.
    if not matched:
        return None  # Configured plugin not installed/available: no fallback.
    if len(matched) != 1:
        raise ValueError("ambiguous_consolidator_declaration")
    selected = matched[0]
    return ExplicitSelection(
        canonical_type=canonical_type, scope=scope,
        plugin_id=selected.plugin_id, version=selected.version,
        schema_id=selected.schema_id,
    )
