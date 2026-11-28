"""Local multi-evidence package using the existing capture ZIP and public kit."""
import json
from hashlib import sha256
from zipfile import ZIP_STORED, ZipFile, BadZipFile
from io import BytesIO

from .capture_export import build_capture_zip
from .capture_devkit import core_contract_kit
from .capture_vault import VaultError
from ..http_trace import HTTP_TRACE_ZIP_LIMIT


def build_health_bundle(rows, *, include_binary, correlated_journal=None, provider_source=None, scope_provider=None):
    if (type(rows) is not tuple or not 1 <= len(rows) <= 25
            or type(include_binary) is not bool):
        raise VaultError("vault_invalid")
    members = {}
    manifest = []
    total = 0
    for number, (capability, payload) in enumerate(rows, 1):
        if (capability not in ("search", "detail", "rights", "loans", "personal_library", "pagination", "availability")
                or type(payload) is not bytes):
            raise VaultError("vault_invalid")
        total += len(payload)
        if total > HTTP_TRACE_ZIP_LIMIT:
            raise VaultError("private_zip_limit")
        name = f"captures/{number:02d}.zip"
        members[name] = payload
        evidence = {"member": name, "capability": capability,
                    "sha256": sha256(payload).hexdigest(),
                    "observed_provider_version": "unknown"}
        # Metadata is part of the authenticated scoped archive, not evidence
        # of the version of any locally installed or exported source code.
        try:
            with ZipFile(BytesIO(payload)) as archive:
                if archive.getinfo("index.json").file_size > 8 * 1024 * 1024:
                    raise ValueError
                meta = json.loads(archive.read("index.json")).get("health_observation")
            if (type(meta) is dict and meta.get("schema") == "cifereca.health-observation/1"
                    and meta.get("capability") == capability):
                for key in ("version", "observed_at", "outcome"):
                    value = meta.get(key)
                    if type(value) is str and 0 < len(value) <= 256 and value.isprintable():
                        evidence["observed_provider_version" if key == "version" else key] = value
        except (BadZipFile, KeyError, ValueError, TypeError, AttributeError):
            pass
        manifest.append(evidence)
    members.update(core_contract_kit())
    journal_status = "not_included"
    if correlated_journal is not None:
        if (type(correlated_journal) is not dict
                or correlated_journal.get("schema") != "cifereca.provider-health-journal/1"
                or correlated_journal.get("coverage") not in ("partial", "retained_matches")):
            raise VaultError("vault_invalid")
        journal_bytes = json.dumps(correlated_journal, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(journal_bytes) > 1_100_000:
            raise VaultError("private_zip_limit")
        members["journal/correlated.json"] = journal_bytes
        journal_status = correlated_journal["coverage"]
    source_status = "not_included"
    source_version = "not_attested_in_bundle"
    if provider_source is not None:
        files = provider_source["files"]
        source_manifest = {key: value for key, value in provider_source.items() if key != "files"}
        source_manifest["files"] = []
        for name, data in sorted(files.items()):
            member = "provider-source/" + name
            members[member] = data
            source_manifest["files"].append({"member": member, "sha256": sha256(data).hexdigest()})
        members["provider-source/manifest.json"] = json.dumps(source_manifest, indent=2).encode()
        source_status = "current_installed_sources"
        source_version = provider_source["version"]
    members["manifest.json"] = json.dumps({
        "schema": "cifereca.provider-health-bundle/1",
        "include_binary": include_binary,
        "scope": {"provider_id": scope_provider, "single_private_account": True}
                 if scope_provider is not None else None,
        "captures": manifest,
        "provider_source": source_status,
        "provider_version": source_version,
        "correlated_logs": journal_status,
        "business_comparison": "not_attested",
    }, indent=2).encode()
    members["README.txt"] = (
        "PRIVATE: raw credentials, URLs and personal data may be present.\n"
        "Review locally before sharing. No automatic upload or correction.\n"
        "Read the public Core contract kit. Compare only equivalent exact-scope "
        "observations; distinguish unknown, attested empty and parser regression.\n"
        "Provider source, if included, is a snapshot of current installed files, not historical source. "
        "Review it before sharing. Consult journal coverage in the manifest; "
        "retained matches are not proof of a complete historical journal. Observed versions "
        "in the manifest do not attest the version of available source code; do not infer "
        "a root cause or claim a successful replay from HTTP status alone.\n"
        "Do not execute captured requests against a real supplier.\n"
    ).encode()
    return build_capture_zip(members, max_bytes=HTTP_TRACE_ZIP_LIMIT,
                             compression=ZIP_STORED)
