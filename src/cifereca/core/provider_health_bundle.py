"""Local multi-evidence package using the existing capture ZIP and public kit."""
import json
from hashlib import sha256
from zipfile import ZIP_STORED, ZipFile, BadZipFile
from io import BytesIO

from .capture_export import build_capture_zip
from .capture_devkit import core_contract_kit
from .capture_vault import VaultError
from ..http_trace import HTTP_TRACE_ZIP_LIMIT


def build_health_bundle(rows, *, include_binary):
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
    members["manifest.json"] = json.dumps({
        "schema": "cifereca.provider-health-bundle/1",
        "include_binary": include_binary,
        "captures": manifest,
        "provider_source": "not_included",
        "provider_version": "not_attested_in_bundle",
        "correlated_logs": "not_included",
        "business_comparison": "not_attested",
    }, indent=2).encode()
    members["README.txt"] = (
        "PRIVATE: raw credentials, URLs and personal data may be present.\n"
        "Review locally before sharing. No automatic upload or correction.\n"
        "Read the public Core contract kit. Compare only equivalent exact-scope "
        "observations; distinguish unknown, attested empty and parser regression.\n"
        "Provider source and correlated logs are not included. Observed versions "
        "in the manifest do not attest the version of available source code; do not infer "
        "a root cause or claim a successful replay from HTTP status alone.\n"
        "Do not execute captured requests against a real supplier.\n"
    ).encode()
    return build_capture_zip(members, max_bytes=HTTP_TRACE_ZIP_LIMIT,
                             compression=ZIP_STORED)
