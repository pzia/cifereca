"""P1 #4093: bounded, private GUI session ZIP (no provider or network I/O).

Only consumes an exact-process journal snapshot plus HTTP_TRACE.snapshot().
Preserves RAW sensitive request/response data. Never auto-uploads or redacts
the local opt-in evidence. No ZIP nesting, extraction, or network replays.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import codecs
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import zipfile
import zlib

_FORMAT = "cifereca-capture-session/1"
_MAX_HTML_BYTES = 2 * 1024 * 1024
_MAX_EXCHANGES = 1_000
_MAX_JOURNAL_BYTES = 64 * 1024 * 1024
_MAX_HTTP_BYTES = 40 * 1024 * 1024
_REF = re.compile(r"[0-9a-f]{32}\Z")
_DESCRIPTION = (
    "CIFERECA - CAPTURE PRIVEE DE SESSION\n"
    "ATTENTION : journal et trafic HTTP BRUTS (identifiants, cookies, "
    "tokens, URLs signees, HTML prive). Aucun anonymisation appliquee.\n"
    "Ce fichier peut etre synchronise par le dossier Telechargements de "
    "votre poste. Ne pas le televerser ou partager sans revue.\n"
    "logs/journal.jsonl contient seulement les lignes de cette instance entre "
    "activation et arret de la capture.\n"
    "http/index.json et http/bodies/ contiennent les observations HTTPX "
    "instrumentees, pas tous les transports applicatifs.\n"
    "html/ contient uniquement des copies decodees de reponses HTML "
    "entierement observees. Les corps transport restent dans http/bodies/.\n"
    "manifest.json fournit les SHA-256 des autres membres. Les preuves HTTP "
    "n'attestent ni un emprunt ni une acquisition finalisee.\n"
)


def _as_json(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True,
                       allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_destination(directory: Path) -> Path:
    if not isinstance(directory, Path):
        raise ValueError("capture_directory_invalid")
    parent = Path(os.path.abspath(directory.expanduser()))
    if (not parent.is_dir() or
            any(part.is_symlink() for part in (parent, *parent.parents))):
        raise ValueError("capture_directory_unavailable")
    return parent


def _headers(meta: object) -> dict[str, str]:
    if type(meta) is not dict:
        return {}
    result: dict[str, str] = {}
    pairs = meta.get("headers")
    if type(pairs) is not list:
        return result
    for pair in pairs:
        if (type(pair) in (list, tuple) and len(pair) == 2
                and all(type(v) is str for v in pair)):
            result[pair[0].lower()] = pair[1]
    return result


def _decode_html_body(data: bytes, response: object, representation: object) -> bytes | None:
    headers = _headers(response)
    mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in {"text/html", "application/xhtml+xml"}:
        return None
    if representation != "httpx_buffered_content":
        coding = headers.get("content-encoding", "identity").strip().lower()
        if coding in ("gzip", "x-gzip"):
            try:
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = decoder.decompress(data, _MAX_HTML_BYTES + 1)
                if not decoder.eof or decoder.unconsumed_tail:
                    return None
            except zlib.error:
                return None
        elif coding == "deflate":
            try:
                decoder = zlib.decompressobj()
                data = decoder.decompress(data, _MAX_HTML_BYTES + 1)
                if not decoder.eof or decoder.unconsumed_tail:
                    return None
            except zlib.error:
                return None
        elif coding not in ("", "identity"):
            # Brotli or an unknown codec must never be treated as decoded HTML.
            return None
    if len(data) > _MAX_HTML_BYTES:
        return None
    charset = "utf-8"
    for token in headers.get("content-type", "").split(";")[1:]:
        k, sep, v = token.strip().partition("=")
        if sep and k.lower() == "charset":
            charset = v.strip().strip('"').strip("'")
            break
    if len(charset) > 64:
        return None
    try:
        codecs.lookup(charset)
        return data.decode(charset, errors="strict").encode("utf-8")
    except (LookupError, UnicodeError, ValueError):
        return None


def _http_members(snapshot: dict[str, object]):
    """Clone the Trace public on-disk schema without changing its RAM snapshot."""
    if type(snapshot) is not dict or snapshot.get("schema") != "cifereca.http-trace/1":
        raise ValueError("capture_http_snapshot_invalid")
    exchanges = snapshot.get("exchanges")
    if type(exchanges) is not list or len(exchanges) > _MAX_EXCHANGES:
        raise ValueError("capture_http_snapshot_invalid")
    index: dict[str, object] = {
        key: value for key, value in snapshot.items() if key != "exchanges"
    }
    index["exchanges"] = []
    members: dict[str, bytes] = {}
    html: list[dict[str, str]] = []
    html_unavailable: list[dict[str, str]] = []
    incomplete = False
    total_http = 0
    seen_refs: set[str] = set()
    for entry in exchanges:
        if type(entry) is not dict or not _REF.fullmatch(str(entry.get("exchange_ref", ""))):
            raise ValueError("capture_http_snapshot_invalid")
        ref = entry["exchange_ref"]
        if ref in seen_refs:
            raise ValueError("capture_http_snapshot_invalid")
        seen_refs.add(ref)
        record = {key: value for key, value in entry.items()
                  if key not in ("request_body", "response_body")}
        for side in ("request", "response"):
            key = side + "_body"
            body = entry.get(key)
            if type(body) is not dict:
                raise ValueError("capture_http_snapshot_invalid")
            raw = body.get("data")
            if type(raw) not in (bytes, bytearray):
                raise ValueError("capture_http_snapshot_invalid")
            data = bytes(raw)
            total_http += len(data)
            if total_http > _MAX_HTTP_BYTES:
                raise ValueError("capture_http_size_limit")
            filename = f"http/bodies/{ref}-{side}.bin"
            members[filename] = data
            record[key] = {k: v for k, v in body.items() if k != "data"}
            record[key].update({
                "blob": filename, "stored_bytes": len(data),
                "sha256": sha256(data).hexdigest(),
            })
            state = body.get("state")
            if state not in ("complete",):
                incomplete = True
            if side == "response":
                mime = _headers(entry.get("response")).get("content-type", "")
                if mime.split(";", 1)[0].strip().lower() in (
                    "text/html", "application/xhtml+xml",
                ):
                    if state == "complete":
                        projected = _decode_html_body(
                            data, entry.get("response"), body.get("representation"),
                        )
                    else:
                        projected = None
                    if projected is None:
                        incomplete = True
                        html_unavailable.append({
                            "exchange_ref": ref,
                            "reason": "body_incomplete" if state != "complete"
                            else "encoding_unavailable",
                        })
                    else:
                        html_name = f"html/{ref}.html"
                        members[html_name] = projected
                        html.append({"exchange_ref": ref, "member": html_name})
        index["exchanges"].append(record)
    index["html_projections"] = html
    index["html_unavailable"] = html_unavailable
    members["http/index.json"] = _as_json(index)
    omitted = snapshot.get("omitted_exchanges", 0)
    if type(omitted) is not int or omitted < 0:
        raise ValueError("capture_http_snapshot_invalid")
    return members, incomplete or omitted > 0, omitted, len(exchanges), len(html), len(html_unavailable)


def export_capture_session(
    directory: Path, *, journal: dict[str, object],
    http_snapshot: dict[str, object], app_exit_code: int,
    build_identity: dict[str, object] | None = None,
    trace_active_at_finish: bool = True,
    trace_uninterrupted: bool = True,
) -> Path:
    """Publish one exclusively created 0600 ZIP in the chosen directory.

    The caller must call CaptureSessionJournal.finish() *after* GUI shutdown
    and snapshot HTTP_TRACE before the Core/Trace data can be destroyed.
    """
    parent = _safe_destination(directory)
    if (type(journal) is not dict
            or journal.get("format") != "cifereca-capture-session-journal"
            or type(journal.get("raw")) is not bytes
            or len(journal["raw"]) > _MAX_JOURNAL_BYTES
            or journal.get("coverage") not in ("complete", "partial")
            or type(app_exit_code) is not int):
        raise ValueError("capture_journal_snapshot_invalid")
    members, http_incomplete, omitted, count, html_count, html_unavailable = _http_members(http_snapshot)
    members["logs/journal.jsonl"] = journal["raw"]
    members["README.txt"] = _DESCRIPTION.encode("utf-8")
    now = datetime.now().astimezone()
    started = journal["started_at_utc"]
    finished = journal["finished_at_utc"]
    if not all(type(v) is str for v in (started, finished)):
        raise ValueError("capture_journal_snapshot_invalid")
    partial = (
        journal["coverage"] != "complete" or http_incomplete
        or trace_active_at_finish is not True or trace_uninterrupted is not True
    )
    manifest = {
        "format": _FORMAT, "schema_version": 1,
        "session_ref": journal["session_ref"],
        "process_id": journal["process_id"],
        "capture_started_at_utc": started,
        "capture_finished_at_utc": finished,
        "exported_at_utc": now.astimezone(timezone.utc).isoformat(),
        "exported_at_local": now.isoformat(),
        "build_identity": build_identity or {"status": "unavailable"},
        "app_exit_code": app_exit_code,
        "coverage": "partial" if partial else "instrumented_only",
        "journal_coverage": journal["coverage"],
        "journal_records": journal["record_count"],
        "journal_omitted_records": journal["omitted_records"],
        "http_coverage": "partial" if (
            http_incomplete or trace_active_at_finish is not True
            or trace_uninterrupted is not True
        ) else "instrumented_only",
        "trace_interrupted": trace_uninterrupted is not True,
        "http_omitted_exchanges": omitted,
        "http_exchanges": count,
        "html_projected": html_count,
        "html_unavailable": html_unavailable,
        "not_covered": ["non_instrumented_http", "external_browser",
                        "subprocess", "requests_urllib"],
        "members": {name: {"sha256": sha256(content).hexdigest(),
                           "bytes": len(content)}
                    for name, content in members.items()},
    }
    members["manifest.json"] = _as_json(manifest)
    filename = (
        f"cifereca-capture-{now:%Y%m%d-%H%M%S}-"
        f"{secrets.token_hex(6)}.zip"
    )
    destination = parent / filename
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".cifereca-capture-",
                                    suffix=".tmp", dir=parent)
        tmp = Path(name)
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), 0o600)
            with zipfile.ZipFile(file, mode="w", compression=zipfile.ZIP_DEFLATED,
                                 allowZip64=False) as archive:
                for member, body in members.items():
                    archive.writestr(member, body)
            file.flush()
            os.fsync(file.fileno())
        if destination.is_symlink() or destination.exists():
            raise FileExistsError("capture_destination_exists")
        os.link(tmp, destination, follow_symlinks=False)
        return destination
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)



_ARCHIVE_BLOB = re.compile(r"http/bodies/([0-9a-f]{32})-(request|response)\.bin\Z")
_ARCHIVE_HTML = re.compile(r"html/([0-9a-f]{32})\.html\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ARCHIVE_FIXED = frozenset({
    "manifest.json", "README.txt", "logs/journal.jsonl", "http/index.json",
})


def _crosscheck_capture_index(archive: zipfile.ZipFile, names: set[str],
                              manifest: dict[str, object]) -> None:
    """Reject coherent-hash archives with forged/orphan HTTP/HTML references.

    A valid digest alone is not evidence that a body belongs to the indexed
    exchange. This check never extracts files or follows embedded paths.
    """
    expected_blobs: set[str] = set()
    expected_html: set[str] = set()
    index_info = archive.getinfo("http/index.json")
    if not 0 < index_info.file_size <= _MAX_HTTP_BYTES:
        raise ValueError("capture_archive_invalid")
    index = json.loads(archive.read(index_info))
    if (type(index) is not dict
            or index.get("schema") != "cifereca.http-trace/1"
            or type(index.get("exchanges")) is not list
            or len(index["exchanges"]) > _MAX_EXCHANGES
            or type(index.get("html_projections")) is not list
            or len(index["html_projections"]) > _MAX_EXCHANGES):
        raise ValueError("capture_archive_invalid")
    refs: set[str] = set()
    for row in index["exchanges"]:
        if type(row) is not dict:
            raise ValueError("capture_archive_invalid")
        ref = row.get("exchange_ref")
        if type(ref) is not str or _REF.fullmatch(ref) is None or ref in refs:
            raise ValueError("capture_archive_invalid")
        refs.add(ref)
        for side in ("request", "response"):
            body = row.get(side + "_body")
            blob = f"http/bodies/{ref}-{side}.bin"
            meta = manifest["members"].get(blob)
            if (type(body) is not dict or body.get("blob") != blob
                    or type(body.get("stored_bytes")) is not int
                    or type(body.get("sha256")) is not str
                    or _SHA256.fullmatch(body["sha256"]) is None
                    or type(meta) is not dict
                    or body["stored_bytes"] != meta.get("bytes")
                    or body["sha256"] != meta.get("sha256")):
                raise ValueError("capture_archive_invalid")
            expected_blobs.add(blob)
    for row in index["html_projections"]:
        if type(row) is not dict:
            raise ValueError("capture_archive_invalid")
        ref = row.get("exchange_ref")
        member = row.get("member")
        if (type(ref) is not str or ref not in refs
                or member != f"html/{ref}.html" or member in expected_html):
            raise ValueError("capture_archive_invalid")
        expected_html.add(member)
    if (type(manifest.get("http_exchanges")) is not int
            or manifest["http_exchanges"] != len(refs)
            or type(manifest.get("html_projected")) is not int
            or manifest["html_projected"] != len(expected_html)):
        raise ValueError("capture_archive_invalid")
    actual_blobs = {name for name in names if _ARCHIVE_BLOB.fullmatch(name)}
    actual_html = {name for name in names if _ARCHIVE_HTML.fullmatch(name)}
    if (actual_blobs != expected_blobs or actual_html != expected_html
            or names != _ARCHIVE_FIXED | actual_blobs | actual_html):
        raise ValueError("capture_archive_invalid")


def validate_capture_session(path: Path) -> dict[str, object]:
    """Bounded, read-only hash/structure check. Never extract or upload data."""
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("capture_archive_invalid")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if (not 3 <= len(names) <= 2 * _MAX_EXCHANGES + _MAX_EXCHANGES + 4
                    or len(set(names)) != len(names)
                    or any(item.is_dir() or "\\" in item.filename
                           or ".." in item.filename.split("/")
                           or item.filename.startswith("/")
                           or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK
                           for item in infos)):
                raise ValueError("capture_archive_invalid")
            if not {"manifest.json", "logs/journal.jsonl", "http/index.json",
                    "README.txt"}.issubset(names):
                raise ValueError("capture_archive_invalid")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > 512 * 1024:
                raise ValueError("capture_archive_invalid")
            data = archive.read("manifest.json")
            if len(data) > 512 * 1024:
                raise ValueError("capture_archive_invalid")
            manifest = json.loads(data)
            if (type(manifest) is not dict
                    or manifest.get("format") != _FORMAT
                    or type(manifest.get("members")) is not dict
                    or set(manifest["members"]) != set(names) - {"manifest.json"}):
                raise ValueError("capture_archive_invalid")
            total_size = sum(info.file_size for info in infos)
            if total_size > 128 * 1024 * 1024:
                raise ValueError("capture_archive_invalid")
            for info in infos:
                if info.filename == "manifest.json":
                    continue
                expected = manifest["members"][info.filename]
                if (type(expected) is not dict or type(expected.get("bytes")) is not int
                        or expected["bytes"] != info.file_size
                        or info.file_size > _MAX_JOURNAL_BYTES
                        or type(expected.get("sha256")) is not str):
                    raise ValueError("capture_archive_invalid")
                digest = sha256()
                with archive.open(info) as raw:
                    while chunk := raw.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != expected["sha256"]:
                    raise ValueError("capture_archive_invalid")
            _crosscheck_capture_index(archive, set(names), manifest)
            return {"format": _FORMAT, "status": "valid",
                    "coverage": manifest.get("coverage"),
                    "members": len(names)}
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, EOFError,
            RuntimeError, KeyError, TypeError) as exc:
        raise ValueError("capture_archive_invalid") from exc
