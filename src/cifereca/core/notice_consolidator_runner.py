"""#4981: bounded, explicitly selected Notice C consolidator execution.

The installed source is a Core-vetted plugin declaration. Supplier rows never
choose code, plugin, scope, schema, entrypoint, identity or execution policy.
The Linux worker receives only already-admitted descriptive evidence and runs
after OS syscall confinement; no HTTP, SQLite, filesystem, credentials or
rights broker enters the worker. Core validates ALL returned provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from .notice_canonical_text_schema import select_text_schema, validate_text_schema_result
from .notice_consolidation_inputs import admit_descriptive_facts
from .notice_consolidation_lineage import descriptive_lineage_key
from .notice_consolidation_result_validation import validate_descriptive_result
from .notice_consolidator_selection import select_consolidator

MAX_SCRIPT_BYTES = 32 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
WORKER_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class InstalledTextConsolidator:
    """A trusted installed-plugin binding, NOT a source-supplied definition.

    Caller must obtain this from Core's verified installed plugin registry.
    The content hash pins the reviewed, installed source. The worker does not
    load Python modules from disk or evaluate code before sandbox activation.
    """

    canonical_type: str
    scope: str
    plugin_id: str
    version: str
    schema_id: str
    source: str
    source_sha256: str


def _run_worker(wire: bytes) -> object:
    try:
        completed = subprocess.run(
            [sys.executable, "-I",
             str(Path(__file__).with_name("notice_consolidator_worker.py"))],
            input=wire, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=WORKER_TIMEOUT_SECONDS, check=False,
            # No user credentials or source-dependent environment is exposed.
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("consolidator_execution_unavailable") from None
    if (completed.returncode != 0
            or len(completed.stdout) > MAX_RESPONSE_BYTES):
        raise ValueError("consolidator_execution_unavailable")
    try:
        response = json.loads(completed.stdout)
    except (ValueError, UnicodeError):
        raise ValueError("consolidator_output_invalid") from None
    if (type(response) is not dict or set(response) != {"ok", "result"}
            or response["ok"] is not True):
        raise ValueError("consolidator_execution_failed")
    return response["result"]


def run_registered_text_consolidator(
    *, declarations: object, text_schemas: object,
    installed: InstalledTextConsolidator,
    canonical_type: str, scope: str,
    selected_plugin_id: str | None, selected_version: str | None,
    target_edition_id: str, source_facts: object,
    attested_strong_links: object,
):
    """Run one explicitly selected pure text plugin with double-run stability.

    No selection, unavailable version or schema, malformed lineage or invalid
    installed binding prevents execution. A type-specific plugin chooses its
    value, but Core alone validates schema, provenance and edition. The
    plugin cannot create a right, save a notice or request network access.

    Two fresh confined workers must produce exactly equal JSON results for
    identical admitted evidence. A timeout, crash, nondeterministic output,
    absence of sandbox support or invalid output fails closed.
    """
    selected = select_consolidator(
        declarations, canonical_type=canonical_type, scope=scope,
        selected_plugin_id=selected_plugin_id,
        selected_version=selected_version,
    )
    if selected is None:
        raise ValueError("consolidator_not_selected")
    schema = select_text_schema(text_schemas, selection=selected)
    if schema is None:
        raise ValueError("canonical_text_schema_not_installed")
    if (type(installed) is not InstalledTextConsolidator
            or (installed.canonical_type, installed.scope, installed.plugin_id,
                installed.version, installed.schema_id) != (
                    selected.canonical_type, selected.scope,
                    selected.plugin_id, selected.version, selected.schema_id,
                )
            or type(installed.source) is not str
            or type(installed.source_sha256) is not str):
        raise ValueError("installed_consolidator_mismatch")
    try:
        source_bytes = installed.source.encode("utf-8")
    except UnicodeError:
        raise ValueError("installed_consolidator_mismatch") from None
    if (not 0 < len(source_bytes) <= MAX_SCRIPT_BYTES
            or sha256(source_bytes).hexdigest() != installed.source_sha256):
        raise ValueError("installed_consolidator_mismatch")

    admitted = admit_descriptive_facts(
        target_edition_id=target_edition_id,
        canonical_type=canonical_type,
        consolidator_id=selected.plugin_id,
        consolidator_version=selected.version,
        source_facts=source_facts,
        attested_strong_links=attested_strong_links,
    )
    # Public DTOs are forgeable; only this Core lineage gate is authoritative.
    descriptive_lineage_key(selection=selected, admitted=admitted)
    if not admitted.exact and not admitted.inherited:
        return None

    def fact_json(fact):
        return {
            "source_id": fact.source_id,
            "canonical_type": fact.canonical_type,
            "edition_id": fact.edition_id,
            "evidence_id": fact.evidence_id,
            "value": fact.value,
            "source_revision": fact.source_revision,
            "relation": fact.relation,
            "inherited": fact.inherited,
            "relation_proof_id": fact.relation_proof_id,
        }

    facts = {
        "target_edition_id": admitted.target_edition_id,
        "canonical_type": selected.canonical_type,
        "schema_id": selected.schema_id,
        "exact": [fact_json(fact) for fact in admitted.exact],
        "inherited": [fact_json(fact) for fact in admitted.inherited],
    }
    wire = json.dumps({
        "source": installed.source,
        "source_sha256": installed.source_sha256,
        "facts": facts,
    }, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(wire) > MAX_REQUEST_BYTES:
        raise ValueError("consolidator_input_unbounded")

    first = _run_worker(wire)
    second = _run_worker(wire)
    if first != second or type(first) is not type(second):
        raise ValueError("non_deterministic_consolidator")
    checked = validate_descriptive_result(
        selection=selected, admitted=admitted, proposed=first,
    )
    if checked is None:
        return None
    return validate_text_schema_result(
        checked, schema=schema, selection=selected,
    )


__all__ = ["InstalledTextConsolidator", "run_registered_text_consolidator"]
