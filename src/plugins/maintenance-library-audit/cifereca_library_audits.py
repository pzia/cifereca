from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePath
from typing import Any
import errno
import hashlib
import os
import stat
import zipfile
from urllib.parse import unquote, urlsplit

from cifereca.plugin_api import (
    MaintenanceContext,
    MaintenancePlan,
    MaintenanceResult,
    MaintenanceToolInfo,
    normalize_language_tag,
)


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _cover_value(row: Mapping[str, Any]) -> str:
    meta = _metadata(row)
    for key in ("thumbnail_url", "cover_url", "image_url", "cover", "thumbnail"):
        value = row.get(key)
        if not value:
            value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _encoded_cover_has_control(value: str) -> bool:
    """Inspect nested percent-encodings without decoding URLs for display.

    A doubly encoded newline becomes a control character after a second
    downstream decoding step. Ordinary percent encodings remain valid.
    """
    for _ in range(4):
        decoded = unquote(value)
        if decoded == value:
            return False
        if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
            return True
        value = decoded
    return False


def _relative_cover_has_parent_segment(value: str) -> bool:
    """Reject path traversal in a relative cover reference, including encodings.

    This is an offline audit, not a path resolver. A parent segment is
    ambiguous without the caller's content root; never present it as a
    verified relative thumbnail. Keep ordinary nested filenames valid.
    """
    for _ in range(5):
        if ".." in value.replace("\\", "/").split("/"):
            return True
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return False


def _language_values(row: Mapping[str, Any]) -> tuple[list[str], int]:
    """Return only text language tags and count malformed non-text values.

    Core catalogue fields are untrusted. Coercing arbitrary objects with
    str(item) can execute custom code or invent language tags from booleans,
    nested objects, or private data; neither belongs in this read-only audit.
    Empty strings and absent values remain missing rather than invalid.
    """
    meta = _metadata(row)
    values: list[str] = []
    malformed = 0
    for container in (row, meta):
        for key in ("language", "languages", "lang", "langs"):
            value = container.get(key)
            if type(value) is str:
                if value.strip():
                    values.append(value.strip())
            elif type(value) in (list, tuple, set):
                for item in value:
                    if type(item) is str:
                        if item.strip():
                            values.append(item.strip())
                    else:
                        malformed += 1
            elif value is not None:
                malformed += 1
    return list(dict.fromkeys(values)), malformed


# Bounds apply to ZIP central-directory *declared* sizes before any member
# is decompressed. Normal EPUBs are much smaller; extremely large collections
# should be inspected by a dedicated, resource-budgeted tool.
_MAX_EPUB_MEMBERS = 8192
_MAX_EPUB_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_EPUB_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024


def _file_integrity_issues(path: str | Path, media_format: str = "", *, expected_size: int = 0, expected_sha256: str = "") -> tuple[str, ...]:
    target = Path(path)
    # A dangling link is a symlink, not a missing book. In particular, do
    # not hash or unpack its target, which may lie outside the library.
    if target.is_symlink():
        return ("symlink",)
    if not target.exists():
        return ("missing",)
    if not target.is_file():
        return ("not_file",)

    issues: list[str] = []
    # Open the same inode once for size, SHA and structural checks. O_NOFOLLOW
    # avoids following a link substituted after the initial lstat; NONBLOCK
    # prevents a concurrently substituted FIFO from hanging the audit.
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        # Never silently degrade the security invariant on platforms where
        # no-follow cannot be requested atomically.
        return ("unreadable",)
    flags = os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        return ("symlink",) if exc.errno == errno.ELOOP else ("unreadable",)
    with os.fdopen(fd, "rb") as stream:
        try:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return ("not_file",)
            if expected_size and info.st_size != int(expected_size):
                issues.append("size_mismatch")
            if expected_sha256:
                h = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
                if h.hexdigest().casefold() != str(expected_sha256).removeprefix("sha256:").casefold():
                    issues.append("sha256_mismatch")
            fmt = str(media_format or target.suffix.lstrip(".")).casefold()
            try:
                if fmt in {"epub", "kepub"} or target.name.casefold().endswith((".epub", ".kepub.epub")):
                    stream.seek(0)
                    with zipfile.ZipFile(stream) as archive:
                        members = archive.infolist()
                        total_size = 0
                        exceeds_budget = len(members) > _MAX_EPUB_MEMBERS
                        for member in members:
                            size = member.file_size
                            if size < 0 or size > _MAX_EPUB_MEMBER_BYTES:
                                exceeds_budget = True
                                break
                            total_size += size
                            if total_size > _MAX_EPUB_UNCOMPRESSED_BYTES:
                                exceeds_budget = True
                                break
                        if exceeds_budget:
                            # ZIP bombs may be tiny on disk but enormous once
                            # unpacked. Never run testzip() or read() on them.
                            issues.append("archive_limit")
                        else:
                            if archive.testzip() is not None:
                                issues.append("zip_crc")
                            try:
                                mimetype = archive.getinfo("mimetype")
                                # Reading a malformed, giant mimetype entry
                                # would allocate its decompressed size at once.
                                if (
                                    mimetype.file_size != len(b"application/epub+zip")
                                    or archive.read("mimetype") != b"application/epub+zip"
                                ):
                                    issues.append("epub_mimetype")
                                archive.getinfo("META-INF/container.xml")
                            except KeyError:
                                issues.append("epub_structure")
                elif fmt == "pdf" or target.suffix.casefold() == ".pdf":
                    stream.seek(0)
                    if stream.read(5) != b"%PDF-":
                        issues.append("pdf_header")
            except (
                OSError, zipfile.BadZipFile, zipfile.LargeZipFile,
                RuntimeError, EOFError, NotImplementedError, ValueError,
            ):
                # A malformed/unsupported ZIP cannot take down the full
                # library audit or expose its archive member names.
                issues.append("container_invalid")
        except OSError:
            issues.append("unreadable")
    return tuple(dict.fromkeys(issues))

def _plan_tick(control, index: int, total: int, phase: str) -> None:
    """Cooperative, throttled *observed* progress; no book or path strings."""
    if control is None:
        return
    control.check_cancelled()
    # At most ~1 callback per 16 records. Never schedule per-row GTK work
    # for a library of many thousands of books.
    if index == 1 or index % 16 == 0 or index == total:
        control.report(
            current=index - 1, total=total, message=phase,
        )


class _ReadOnlyTool:
    info: MaintenanceToolInfo

    def __init__(self) -> None:
        self.context = None

    def activate(self, context):
        self.context = context
        return self

    def deactivate(self):
        self.context = None

    def describe(self) -> MaintenanceToolInfo:
        return self.info

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        # These audits perform their scan entirely in plan(), where the public
        # contract has no progress callback.  Returning the precomputed result
        # is not an independently measurable one-item operation: no 1/1.
        control.check_cancelled()
        control.report(message=plan.summary)
        control.check_cancelled()
        return MaintenanceResult(
            ok=True,
            changed=0,
            summary=plan.summary,
            warnings=plan.warnings,
            facts=dict(plan.facts),
        )

    def _broker(self, broker_id: str):
        if self.context is None:
            raise RuntimeError("outil non activé")
        broker = self.context.broker(broker_id)
        if broker is None:
            raise RuntimeError(f"broker {broker_id} indisponible")
        return broker


class CoverAuditTool(_ReadOnlyTool):
    info = MaintenanceToolInfo(
        id="covers.audit",
        title="Vérifier les couvertures",
        description="Compte les notices sans couverture et les références de couverture manifestement invalides.",
        icon_name="image-x-generic-symbolic",
        scope="library",
        danger_level="read-only",
        supports_dry_run=True,
        cancellable=True,
    )

    def plan(self, context: MaintenanceContext) -> MaintenancePlan:
        return self._plan_scan(context, None)

    def plan_with_progress(self, context: MaintenanceContext, control) -> MaintenancePlan:
        return self._plan_scan(context, control)

    def _plan_scan(self, context: MaintenanceContext, control) -> MaintenancePlan:
        if control is not None:
            control.check_cancelled()
            control.report(message="Couvertures : examen des notices : chargement")
        entries = _rows(self._broker("catalog").catalog_entries(identity_id=context.identity_id))
        missing = 0
        invalid = 0
        samples: list[str] = []
        for notice_index, row in enumerate(entries, start=1):
            _plan_tick(control, notice_index, len(entries), "Couvertures : examen des notices")
            try:
                cover = _cover_value(row)
            except Exception:
                # Row mappings and string subclasses may throw while reading
                # or normalizing a cover. Classify that one reference as
                # invalid rather than stopping all subsequent catalogue rows.
                invalid += 1
                if len(samples) < 12:
                    samples.append(f"Couverture non reconnue : notice n°{notice_index}")
                continue
            # Preview warnings cross the UI/diagnostic boundary. Never embed
            # free-form titles, local paths or untrusted cover URLs there.
            if not cover:
                missing += 1
                if len(samples) < 12:
                    samples.append(f"Sans couverture : notice n°{notice_index}")
                continue
            try:
                # The audit stays bounded for a corrupted remote metadata
                # field. Nested decoding is only for rejection, with no I/O.
                if len(cover) > 8192 or _encoded_cover_has_control(cover):
                    invalid += 1
                    if len(samples) < 12:
                        samples.append(f"Couverture non reconnue : notice n°{notice_index}")
                    continue
                parsed = urlsplit(cover)
                scheme = parsed.scheme.casefold()
                # An allowed scheme alone is not evidence of a usable cover.
                # Catch absent hosts, malformed ports, embedded credentials,
                # remote file:// hosts and control characters. Preserve local
                # relative cover paths used by legacy library entries.
                suspicious = (
                    any(ord(ch) < 32 or ord(ch) == 127 for ch in cover)
                    # Encoded CR/LF/NUL are control characters too. The
                    # reference may be decoded by a later URL consumer.
                    or any(
                        len(cover) >= pos + 3
                        and all(c in "0123456789abcdefABCDEF" for c in cover[pos + 1:pos + 3])
                        and (
                            int(cover[pos + 1:pos + 3], 16) < 32
                            or int(cover[pos + 1:pos + 3], 16) == 127
                        )
                        for pos, ch in enumerate(cover) if ch == "%"
                    )
                    or bool(scheme and scheme not in {"http", "https", "file"})
                )
                if scheme in {"http", "https"}:
                    suspicious = suspicious or (
                        not parsed.hostname
                        or parsed.username is not None
                        or parsed.password is not None
                        or parsed.port == 0
                    )
                    # Accessing .port validates non-numeric/out-of-range ports.
                    _ = parsed.port
                elif scheme == "file":
                    suspicious = suspicious or (
                        parsed.netloc not in {"", "localhost"}
                        or not parsed.path.startswith("/")
                        or parsed.username is not None
                        or parsed.password is not None
                    )
                elif not scheme:
                    suspicious = (
                        suspicious or bool(parsed.netloc)
                        or _relative_cover_has_parent_segment(parsed.path)
                    )
            except Exception:
                # Even a malicious str subclass can fail outside urlsplit's
                # documented ValueError cases. One bad reference must never
                # erase all other cover results or reveal exception details.
                suspicious = True
            if suspicious:
                invalid += 1
                if len(samples) < 12:
                    samples.append(f"Couverture non reconnue : notice n°{notice_index}")
        total = len(entries)
        summary = f"{missing} notice(s) sans couverture, {invalid} référence(s) suspecte(s), sur {total} notice(s)."
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=summary,
            warnings=tuple(samples),
            facts={"entries": total, "missing": missing, "invalid": invalid},
        )


    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        """Never republish an untrusted/stale preview's arbitrary strings.

        Cover previews can be restored from a cache. Plan summaries and
        warnings are not authority to render provider-controlled text in the
        tool's live progress line or in exported diagnostics.
        """
        facts = plan.facts
        valid = (
            plan.tool_id == self.info.id
            and type(facts) is dict
            and set(facts) == {"entries", "missing", "invalid"}
        )
        counts = {}
        if valid:
            for key in ("entries", "missing", "invalid"):
                value = facts.get(key)
                if type(value) is not int or not 0 <= value <= 1_000_000:
                    valid = False
                    break
                counts[key] = value
            if valid and counts["missing"] + counts["invalid"] > counts["entries"]:
                valid = False
        expected_summary = (
            f'{counts["missing"]} notice(s) sans couverture, '
            f'{counts["invalid"]} référence(s) suspecte(s), '
            f'sur {counts["entries"]} notice(s).'
        ) if valid else ""
        if valid:
            valid = type(plan.summary) is str and plan.summary == expected_summary
        safe_warnings = []
        if valid:
            valid = type(plan.warnings) in (tuple, list) and len(plan.warnings) <= 12
            if valid:
                import re
                sample_counts = {"Sans couverture": 0, "Couverture non reconnue": 0}
                last_ordinal = 0
                for warning in plan.warnings:
                    if type(warning) is not str:
                        valid = False
                        break
                    match = re.fullmatch(
                        r"(Sans couverture|Couverture non reconnue) : notice n°([1-9][0-9]{0,6})",
                        warning,
                    )
                    if match is None:
                        valid = False
                        break
                    ordinal = int(match.group(2))
                    # A source row has exactly one cover classification. The
                    # original plan samples at most 12 anomalies in scan
                    # order, never duplicating or reordering their ordinals.
                    if not last_ordinal < ordinal <= counts["entries"]:
                        valid = False
                        break
                    last_ordinal = ordinal
                    sample_counts[match.group(1)] += 1
                    safe_warnings.append(warning)
                if valid:
                    defects = counts["missing"] + counts["invalid"]
                    valid = (
                        len(safe_warnings) == min(12, defects)
                        and sample_counts["Sans couverture"] <= counts["missing"]
                        and sample_counts["Couverture non reconnue"] <= counts["invalid"]
                    )
                    # All anomalies fit in the preview: every one must be
                    # represented. More than 12: only the first 12 are known.
                    if valid and defects <= 12:
                        valid = (
                            sample_counts["Sans couverture"] == counts["missing"]
                            and sample_counts["Couverture non reconnue"] == counts["invalid"]
                        )
        control.check_cancelled()
        if not valid:
            control.report(message="Audit des couvertures impossible : plan invalide")
            control.check_cancelled()
            return MaintenanceResult(
                ok=False,
                changed=0,
                summary="Audit des couvertures non vérifiable : plan invalide.",
                warnings=("Plan de couvertures invalide : relancez l’analyse.",),
                facts={"invalid_plan": 1},
            )
        control.report(message=expected_summary)
        control.check_cancelled()
        return MaintenanceResult(
            ok=True,
            changed=0,
            summary=expected_summary,
            warnings=tuple(safe_warnings),
            facts=counts,
        )


class LanguageAuditTool(_ReadOnlyTool):
    info = MaintenanceToolInfo(
        id="languages.audit",
        title="Vérifier les langues",
        description="Détecte les tags de langue invalides ou non canoniques dans les notices disponibles.",
        icon_name="preferences-desktop-locale-symbolic",
        scope="library",
        danger_level="read-only",
        supports_dry_run=True,
        cancellable=True,
    )

    def plan(self, context: MaintenanceContext) -> MaintenancePlan:
        return self._plan_scan(context, None)

    def plan_with_progress(self, context: MaintenanceContext, control) -> MaintenancePlan:
        return self._plan_scan(context, control)

    def _plan_scan(self, context: MaintenanceContext, control) -> MaintenancePlan:
        if control is not None:
            control.check_cancelled()
            control.report(message="Langues : examen des notices : chargement")
        entries = _rows(self._broker("catalog").catalog_entries(identity_id=context.identity_id))
        seen = 0
        invalid = 0
        noncanonical = 0
        malformed = 0
        normalization_failures = 0
        samples: list[str] = []
        for notice_index, row in enumerate(entries, start=1):
            _plan_tick(control, notice_index, len(entries), "Langues : examen des notices")
            # Tool plans and results can be exported to diagnostics/UI. Neither
            # free-form book titles nor untrusted raw language tags are safe
            # diagnostic labels (both may contain credentials or private paths).
            # Keep only the deterministic catalogue ordinal and issue class.
            try:
                tags, rejected = _language_values(row)
            except Exception:
                # Provider mappings/iterables may throw from get() or
                # iteration. Do not abandon other books or expose any
                # exception class, text, or raw language in diagnostics.
                malformed += 1
                if len(samples) < 12:
                    samples.append(f"Langue non textuelle : notice n°{notice_index}")
                continue
            malformed += rejected
            if rejected and len(samples) < 12:
                samples.append(f"Langue non textuelle : notice n°{notice_index}")
            for raw in tags:
                seen += 1
                try:
                    canonical = normalize_language_tag(raw)
                except Exception:
                    # One malformed provider tag or unexpected Core policy
                    # failure cannot abort the remaining catalogue scan.
                    # Never copy exception messages or their class names.
                    normalization_failures += 1
                    if len(samples) < 12:
                        samples.append(f"Langue non vérifiable : notice n°{notice_index}")
                    continue
                if canonical is not None and type(canonical) is not str:
                    normalization_failures += 1
                    if len(samples) < 12:
                        samples.append(f"Langue non vérifiable : notice n°{notice_index}")
                elif canonical is None:
                    invalid += 1
                    if len(samples) < 12:
                        samples.append(f"Langue invalide : notice n°{notice_index}")
                elif canonical != raw:
                    noncanonical += 1
                    if len(samples) < 12:
                        samples.append(f"Langue non canonique : notice n°{notice_index}")
        summary = f"{seen} valeur(s) de langue ; {invalid} invalide(s), {noncanonical} non canonique(s)."
        if malformed or normalization_failures:
            summary += (
                f" {malformed} valeur(s) non textuelle(s) ;"
                f" {normalization_failures} normalisation(s) indisponible(s)."
            )
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=summary,
            warnings=tuple(samples),
            facts={
                "language_values": seen, "invalid": invalid,
                "noncanonical": noncanonical,
                "malformed": malformed,
                "normalization_failures": normalization_failures,
            },
        )


    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        """Render only language audit facts/ordinals that this tool generates.

        A restored plan is an untrusted input, not a safe UI string source.
        """
        facts = plan.facts
        keys = {
            "language_values", "invalid", "noncanonical",
            "malformed", "normalization_failures",
        }
        valid = plan.tool_id == self.info.id and type(facts) is dict and set(facts) == keys
        counts = {}
        if valid:
            for key in keys:
                value = facts.get(key)
                if type(value) is not int or not 0 <= value <= 1_000_000:
                    valid = False
                    break
                counts[key] = value
        if valid and (
            counts["invalid"] + counts["noncanonical"]
            + counts["normalization_failures"] > counts["language_values"]
        ):
            valid = False
        expected = (
            f'{counts["language_values"]} valeur(s) de langue ; '
            f'{counts["invalid"]} invalide(s), '
            f'{counts["noncanonical"]} non canonique(s).'
        ) if valid else ""
        if valid and (counts["malformed"] or counts["normalization_failures"]):
            expected += (
                f' {counts["malformed"]} valeur(s) non textuelle(s) ;'
                f' {counts["normalization_failures"]} normalisation(s) indisponible(s).'
            )
        if valid:
            valid = type(plan.summary) is str and plan.summary == expected
        safe_warnings = []
        if valid:
            valid = type(plan.warnings) in (tuple, list) and len(plan.warnings) <= 12
            if valid:
                import re
                labels = {
                    "non textuelle": "malformed",
                    "non vérifiable": "normalization_failures",
                    "invalide": "invalid",
                    "non canonique": "noncanonical",
                }
                sample_counts = {key: 0 for key in labels.values()}
                last_ordinal = 0
                for warning in plan.warnings:
                    if type(warning) is not str:
                        valid = False
                        break
                    match = re.fullmatch(
                        r"Langue (non textuelle|non vérifiable|invalide|non canonique) : notice n°([1-9][0-9]{0,6})",
                        warning,
                    )
                    if match is None:
                        valid = False
                        break
                    ordinal = int(match.group(2))
                    # Several tags of one notice can legitimately be faulty.
                    # Notices, however, are scanned in order, never backwards.
                    if ordinal < last_ordinal:
                        valid = False
                        break
                    last_ordinal = ordinal
                    kind = labels[match.group(1)]
                    sample_counts[kind] += 1
                    if sample_counts[kind] > counts[kind]:
                        valid = False
                        break
                    safe_warnings.append(warning)
                # Samples are optional and may omit entire categories,
                # including when fewer than twelve anomalies were counted.
                # Some historical/cached providers only expose an excerpt.
                # Each *present* sample must still be ordered and supported
                # by a corresponding positive, bounded count.
        control.check_cancelled()
        if not valid:
            control.report(message="Audit des langues impossible : plan invalide")
            control.check_cancelled()
            return MaintenanceResult(
                ok=False, changed=0,
                summary="Audit des langues non vérifiable : plan invalide.",
                warnings=("Plan de langues invalide : relancez l’analyse.",),
                facts={"invalid_plan": 1},
            )
        control.report(message=expected)
        control.check_cancelled()
        return MaintenanceResult(
            # Invalid/noncanonical *tags* are audit findings. A failed Core
            # normalizer is different: some language values were not checked,
            # so the scan cannot be reported as an entirely successful audit.
            ok=counts["normalization_failures"] == 0,
            changed=0, summary=expected,
            warnings=tuple(safe_warnings), facts=counts,
        )


class NamingAuditTool(_ReadOnlyTool):
    info = MaintenanceToolInfo(
        id="library.naming.audit",
        title="Auditer les noms de fichiers",
        description="Compare les noms observables à la politique de nommage Core, sans renommer.",
        icon_name="document-properties-symbolic",
        scope="library",
        danger_level="read-only",
        supports_dry_run=True,
        cancellable=True,
    )

    def plan(self, context: MaintenanceContext) -> MaintenancePlan:
        return self._plan_scan(context, None)

    def plan_with_progress(self, context: MaintenanceContext, control) -> MaintenancePlan:
        return self._plan_scan(context, control)

    def _plan_scan(self, context: MaintenanceContext, control) -> MaintenancePlan:
        if control is not None:
            control.check_cancelled()
            control.report(message="Noms : examen des fichiers : chargement")
        entries = _rows(self._broker("metadata").library_entries(identity_id=context.identity_id))
        naming = self._broker("library.naming")
        checked = 0
        mismatch = 0
        unavailable = 0
        policy_failures = 0
        samples: list[str] = []
        for notice_index, row in enumerate(entries, start=1):
            _plan_tick(control, notice_index, len(entries), "Noms : examen des fichiers")
            # Everything from a metadata row (including str(), pathlib and
            # broker output) is untrusted. An exception raised *before* the
            # old preview() try block used to abort the entire batch.
            try:
                title = str(row.get("title") or "").strip()
                current = str(row.get("filename") or row.get("file_name") or "").strip()
                if not current:
                    path = str(row.get("path") or "").strip()
                    current = PurePath(path).name if path else ""
                if not title or not current:
                    unavailable += 1
                    continue
                extension = (
                    PurePath(current).suffix.lstrip(".")
                    or str(row.get("format") or "epub").lower()
                )
                expected = naming.preview(
                    title=title,
                    author=str(row.get("author") or ""),
                    series=str(row.get("series") or row.get("collection") or ""),
                    index=str(row.get("series_index") or row.get("index") or ""),
                    extension=extension,
                )
                # A broker preview is a filename proposal, not an arbitrary
                # object or a complete filesystem path. Never coerce a bool,
                # number or provider-supplied object into a plausible name,
                # and never downgrade an absolute or nested path by basename.
                if type(expected) is not str:
                    raise ValueError("unusable naming policy proposal")
                proposed = expected
                if (
                    not proposed or proposed != proposed.strip()
                    or len(proposed) > 512
                    or proposed in {".", ".."}
                    or "/" in proposed or "\\" in proposed
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in proposed)
                ):
                    raise ValueError("invalid naming policy filename")
                different = PurePath(current).name != proposed
            except Exception:
                # Never include provider values, exception texts or dynamic
                # exception class names in the public preview/progress.
                policy_failures += 1
                if len(samples) < 12:
                    samples.append(f"Politique de nommage indisponible : notice n°{notice_index}")
                continue
            checked += 1
            if different:
                mismatch += 1
                if len(samples) < 12:
                    samples.append(f"Nom non conforme : notice n°{notice_index}")
        summary = f"{mismatch} nom(s) différent(s) de la politique Core sur {checked} fichier(s) vérifiable(s)."
        warnings = list(samples)
        if unavailable:
            warnings.append(f"{unavailable} entrée(s) sans nom/titre public vérifiable.")
        if policy_failures:
            warnings.append(f"{policy_failures} entrée(s) sans proposition valide de la politique Core.")
        warnings.append("Audit uniquement : aucune mutation de placement n'est effectuée.")
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=summary,
            warnings=tuple(warnings),
            facts={
                "checked": checked, "mismatch": mismatch,
                "unavailable": unavailable, "policy_failures": policy_failures,
            },
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        """Defend the live UI boundary against forged cached naming previews."""
        facts = plan.facts
        keys = {"checked", "mismatch", "unavailable", "policy_failures"}
        valid = (
            plan.tool_id == self.info.id
            and type(facts) is dict
            and set(facts) == keys
        )
        counts = {}
        if valid:
            for key in keys:
                value = facts.get(key)
                if type(value) is not int or not 0 <= value <= 1_000_000:
                    valid = False
                    break
                counts[key] = value
        if valid and counts["mismatch"] > counts["checked"]:
            valid = False
        expected = (
            f'{counts["mismatch"]} nom(s) différent(s) de la politique Core '
            f'sur {counts["checked"]} fichier(s) vérifiable(s).'
        ) if valid else ""
        if valid:
            valid = type(plan.summary) is str and plan.summary == expected
        safe_warnings = []
        if valid:
            valid = type(plan.warnings) in (tuple, list) and len(plan.warnings) <= 15
        if valid:
            import re
            # Each sampled notice has exactly one classified outcome. For
            # restored plans, counts alone are not enough: the warning list
            # must reflect the actual bounded, ordered preview policy.
            samples = []
            observed = {"mismatch": 0, "policy_failures": 0}
            last_ordinal = 0
            for warning in plan.warnings:
                if type(warning) is not str:
                    valid = False
                    break
                match = re.fullmatch(
                    r"(Politique de nommage indisponible|Nom non conforme) : notice n°([1-9][0-9]{0,6})",
                    warning,
                )
                if match:
                    ordinal = int(match.group(2))
                    if len(samples) >= 12 or ordinal <= last_ordinal:
                        valid = False
                        break
                    last_ordinal = ordinal
                    kind = (
                        "mismatch" if match.group(1) == "Nom non conforme"
                        else "policy_failures"
                    )
                    observed[kind] += 1
                    if observed[kind] > counts[kind]:
                        valid = False
                        break
                    samples.append(warning)
                else:
                    # Aggregate warnings cannot be interspersed with samples.
                    break
            if valid:
                expected_samples = min(
                    12, counts["mismatch"] + counts["policy_failures"]
                )
                valid = len(samples) == expected_samples
                if valid and expected_samples < 12:
                    valid = (
                        observed["mismatch"] == counts["mismatch"]
                        and observed["policy_failures"] == counts["policy_failures"]
                    )
            if valid:
                tail = []
                if counts["unavailable"]:
                    tail.append(
                        f'{counts["unavailable"]} entrée(s) sans nom/titre public vérifiable.'
                    )
                if counts["policy_failures"]:
                    tail.append(
                        f'{counts["policy_failures"]} entrée(s) sans proposition valide de la politique Core.'
                    )
                tail.append("Audit uniquement : aucune mutation de placement n'est effectuée.")
                valid = list(plan.warnings[len(samples):]) == tail
            if valid:
                safe_warnings = list(plan.warnings)
        control.check_cancelled()
        if not valid:
            control.report(message="Audit des noms impossible : plan invalide")
            control.check_cancelled()
            return MaintenanceResult(
                ok=False, changed=0,
                summary="Audit des noms non vérifiable : plan invalide.",
                warnings=("Plan d’audit des noms invalide : relancez l’analyse.",),
                facts={"invalid_plan": 1},
            )
        control.report(message=expected)
        control.check_cancelled()
        return MaintenanceResult(
            ok=counts["policy_failures"] == 0,
            changed=0, summary=expected,
            warnings=tuple(safe_warnings), facts=counts,
        )


class IntegrityAuditTool(_ReadOnlyTool):
    info = MaintenanceToolInfo(
        id="library.integrity.audit",
        title="Vérifier l’intégrité des fichiers",
        description="Vérifie présence, taille, SHA-256 et structure minimale des EPUB/PDF locaux.",
        icon_name="security-high-symbolic",
        scope="library",
        danger_level="read-only",
        supports_dry_run=True,
        cancellable=True,
    )

    def plan(self, context: MaintenanceContext) -> MaintenancePlan:
        return self._plan_scan(context, None)

    def plan_with_progress(self, context: MaintenanceContext, control) -> MaintenancePlan:
        return self._plan_scan(context, control)

    def _plan_scan(self, context: MaintenanceContext, control) -> MaintenancePlan:
        if control is not None:
            control.check_cancelled()
            control.report(message="intégrité : chargement de l'inventaire")
        metadata=self._broker("metadata")
        library_read=self._broker("library.read")
        candidates=[]
        missing_path=0
        read_failures=0
        reference_failures=0
        rows = _rows(metadata.library_entries(identity_id=context.identity_id))
        for index, row in enumerate(rows, start=1):
            _plan_tick(control, index, len(rows), "Intégrité : pré-inventaire")
            try:
                if not row.get("local"):
                    continue
                raw_path = row.get("local_path")
                if raw_path is None or (type(raw_path) is str and not raw_path.strip()):
                    missing_path += 1
                    continue
                if type(raw_path) is not str:
                    reference_failures += 1
                    continue
                path = raw_path.strip()
                # An explicit poisoned artifact reference cannot be silently
                # downgraded into an unverified filesystem candidate. Never
                # stringify it or pass it to the library.read broker.
                raw_id = row.get("artifact_id")
                if raw_id is not None and (
                    type(raw_id) is not str or
                    (raw_id and _artifact_id(row) != raw_id)
                ):
                    reference_failures += 1
                    continue
                artifact_id = _artifact_id(row)
            except Exception:
                # Provider metadata is not a source of public diagnostics.
                reference_failures += 1
                continue
            artifact = None
            if artifact_id:
                try:
                    artifact = library_read.artifact(artifact_id)
                except Exception:
                    read_failures += 1
            # A broken accessor is also an invalid reference, not a reason
            # to skip unrelated files. Unknown size means "not attested".
            try:
                raw_size = getattr(artifact, "size_bytes", 0)
                if type(raw_size) is int and raw_size >= 0:
                    expected_size = raw_size
                elif (
                    type(raw_size) is str and 1 <= len(raw_size) <= 20
                    and raw_size.isdecimal()
                ):
                    expected_size = int(raw_size)
                elif raw_size is None:
                    expected_size = 0
                else:
                    raise ValueError("invalid reference size")
            except Exception:
                reference_failures += 1
                expected_size = 0
            try:
                media_format = str(
                    row.get("media_format") or getattr(artifact, "format", "") or ""
                )
                expected_sha256 = str(
                    getattr(artifact, "sha256", "") or row.get("sha256") or ""
                )
            except Exception:
                # Never pass partial or poisoned format/hash coordinates to
                # the filesystem scan; other candidates can still run.
                reference_failures += 1
                continue
            candidates.append({
                "path": path,
                "format": media_format,
                "expected_size": expected_size,
                "expected_sha256": expected_sha256,
            })
        warnings=[]
        if missing_path:
            warnings.append(f"{missing_path} entrée(s) locale(s) sans chemin observable.")
        if read_failures:
            warnings.append(
                f"{read_failures} artefact(s) sans métadonnées de référence : broker library.read en erreur."
            )
        if reference_failures:
            warnings.append(
                f"{reference_failures} référence(s) d’intégrité invalide(s) : certains contrôles indisponibles."
            )
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=f"{len(candidates)} fichier(s) local(aux) à vérifier.",
            warnings=tuple(warnings),
            facts={
                "candidates": tuple(candidates),
                "missing_path": missing_path,
                "read_failures": read_failures,
                "reference_failures": reference_failures,
            },
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        # Cached or caller-supplied maintenance plans are untrusted at the
        # execution boundary. In particular, never echo plan.warnings into
        # the operation row: they may contain local paths or credentials.
        facts = plan.facts
        valid = plan.tool_id == self.info.id and type(facts) is dict
        if valid:
            raw_candidates = facts.get("candidates", ())
            valid = type(raw_candidates) in (tuple, list) and len(raw_candidates) <= 100_000
        counts = {}
        if valid:
            for name in ("missing_path", "read_failures", "reference_failures"):
                value = facts.get(name, 0)
                if type(value) is not int or not 0 <= value <= 1_000_000:
                    valid = False
                    break
                counts[name] = value
        if not valid:
            control.check_cancelled()
            control.report(message="Audit d’intégrité impossible : plan invalide")
            control.check_cancelled()
            return MaintenanceResult(
                ok=False, changed=0,
                summary="Audit d’intégrité non vérifiable : plan invalide.",
                warnings=("Plan d’intégrité invalide : relancez l’analyse.",),
                facts={"invalid_plan": 1},
            )
        candidates = tuple(raw_candidates)
        total = len(candidates)
        read_failures = counts["read_failures"]
        reference_failures = counts["reference_failures"]
        warnings = []
        if counts["missing_path"]:
            warnings.append(
                f'{counts["missing_path"]} entrée(s) locale(s) sans chemin observable.'
            )
        if read_failures:
            warnings.append(
                f"{read_failures} artefact(s) sans métadonnées de référence : broker library.read en erreur."
            )
        if reference_failures:
            warnings.append(
                f"{reference_failures} référence(s) d’intégrité invalide(s) : certains contrôles indisponibles."
            )
        invalid=0
        missing=0
        by_issue={}
        # The plan enumerates file candidates but does not examine their bytes.
        # Only the run can report measured progress. An empty batch is not 1/1.
        control.check_cancelled()
        if not total:
            control.report(message="Aucun fichier local à vérifier")
        for index,row in enumerate(candidates,start=1):
            control.check_cancelled()
            control.report(current=index-1,total=total,message=f"Intégrité {index}/{total}")
            # Plans may be cached, restored, or supplied by a caller rather
            # than produced by this exact plugin version. Revalidate runtime
            # candidates: a malformed size must not crash the entire batch,
            # and a non-mapping entry must not select an arbitrary path.
            issues: tuple[str, ...]
            if not isinstance(row, Mapping):
                issues = ("reference_invalid",)
            else:
                try:
                    # Mapping.get(), str() and path objects are not trusted:
                    # a poisoned restored row must fail only its own entry.
                    raw_size = row.get("expected_size")
                    if type(raw_size) is int and raw_size >= 0:
                        size = raw_size
                    elif raw_size is None:
                        size = 0
                    elif (
                        type(raw_size) is str and 1 <= len(raw_size) <= 20
                        and raw_size.isascii() and raw_size.isdecimal()
                    ):
                        size = int(raw_size)
                    else:
                        size = None
                    path = row.get("path")
                    fmt = row.get("format")
                    digest = row.get("expected_sha256")
                    if (
                        size is None or not isinstance(path, (str, Path))
                        or not str(path).strip()
                        or not isinstance(fmt, str)
                        or not isinstance(digest, str)
                    ):
                        issues = ("reference_invalid",)
                    else:
                        issues = _file_integrity_issues(
                            path, fmt, expected_size=size,
                            expected_sha256=digest,
                        )
                except Exception:
                    # No provider exception text/class crosses the diagnostic
                    # boundary, nor can it abort the rest of this audit batch.
                    issues = ("reference_invalid",)
            if issues:
                invalid += 1
                if "missing" in issues: missing += 1
                for issue in issues: by_issue[issue]=by_issue.get(issue,0)+1
                if len(warnings)<100:
                    # Result warnings may be surfaced in the activity UI or
                    # diagnostics. Never disclose a library title, filename,
                    # path, or upstream string while reporting a file failure.
                    warnings.append(f"Fichier {index}/{total} : {', '.join(issues)}")
        # Files without a usable path were excluded by plan(), but still
        # prevent the scan from proving that the local library is healthy.
        # Distinguish inspected anomalies from unavailable checks; neither
        # case is a completed successful audit.
        unavailable = counts["missing_path"] + read_failures + reference_failures
        incomplete = bool(invalid or unavailable)
        # Cancellation during the final inspection must not publish 100%.
        control.check_cancelled()
        if total:
            status = (
                f"Audit d’intégrité incomplet : {invalid} anomalie(s), "
                f"{unavailable} contrôle(s) indisponible(s)"
                if incomplete else "Audit d’intégrité terminé"
            )
            control.report(current=total, total=total, message=status)
        else:
            control.report(message=(
                f"Audit d’intégrité incomplet : {unavailable} contrôle(s) indisponible(s)"
                if incomplete else "Audit d’intégrité terminé : aucun fichier"
            ))
        summary = (
            f"{invalid} fichier(s) en anomalie sur {len(candidates)} vérifié(s), "
            f"dont {missing} manquant(s)."
        )
        if unavailable:
            summary += f" {unavailable} contrôle(s) indisponible(s)."
        return MaintenanceResult(
            ok=not incomplete,
            changed=0,
            summary=summary,
            warnings=tuple(warnings),
            facts={
                "checked":len(candidates),
                "invalid":invalid,
                "missing":missing,
                "missing_path":counts["missing_path"],
                "read_failures":read_failures,
                "reference_failures":reference_failures,
                "issues":by_issue,
            },
        )


COVER_AUDIT = CoverAuditTool()
LANGUAGE_AUDIT = LanguageAuditTool()
NAMING_AUDIT = NamingAuditTool()
INTEGRITY_AUDIT = IntegrityAuditTool()

# Core041 repair tools -------------------------------------------------------
# These tools never mutate the filesystem or storage directly. They only
# describe work and delegate each authorized mutation to library.maintenance.


def _artifact_id(row: Mapping[str, Any]) -> str:
    """Return only a bounded opaque reference, never stringify provider objects.

    Metadata may include broken identifiers or objects whose __str__/__bool__
    raises, blocks, or reveals private text. Core library.read must only
    receive an explicit, plain identifier.
    """
    try:
        value = row.get("artifact_id")
        if value is None or (type(value) is str and not value):
            value = row.get("artifact")
    except Exception:
        return ""
    if (
        type(value) is not str or not 0 < len(value) <= 1024
        or value.strip() != value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return ""
    return value


def _iter_views(metadata, library_read, identity_id: str | None,
                failures: list[str] | None = None, *,
                control=None, phase: str = ""):
    """Enumerate once; the same inventory drives scan count and candidates."""
    seen: set[str] = set()
    rows = _rows(metadata.library_entries(identity_id=identity_id))
    for index, row in enumerate(rows, start=1):
        _plan_tick(control, index, len(rows), phase)
        artifact_id = _artifact_id(row)
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        try:
            view = library_read.for_artifact(artifact_id)
        except Exception:
            if failures is not None:
                # Only the *count* leaves the plan, but even temporary
                # diagnostics should not retain provider exception class
                # names, which can embed credential or private path data.
                failures.append("unavailable")
            continue
        if view is not None:
            yield row, view


class NamingRepairTool(_ReadOnlyTool):
    info = MaintenanceToolInfo(
        id="library.naming.repair",
        title="Renormaliser les noms de fichiers",
        description="Applique la politique de nommage Core aux placements de bibliothèque vérifiables.",
        icon_name="document-save-symbolic",
        scope="library",
        danger_level="repair",
        supports_dry_run=True,
        cancellable=True,
    )

    def plan(self, context: MaintenanceContext) -> MaintenancePlan:
        return self._plan_scan(context, None)

    def plan_with_progress(self, context: MaintenanceContext, control) -> MaintenancePlan:
        return self._plan_scan(context, control)

    def _plan_scan(self, context: MaintenanceContext, control) -> MaintenancePlan:
        if control is not None:
            control.check_cancelled()
            control.report(message="renommage : chargement de l'inventaire")
        metadata = self._broker("metadata")
        naming = self._broker("library.naming")
        library_read = self._broker("library.read")
        placement_ids: list[str] = []
        changes: list[str] = []
        skipped = 0
        policy_failures = 0
        read_failures: list[str] = []
        seen: set[str] = set()

        for row, view in _iter_views(
            metadata, library_read, context.identity_id, read_failures,
            control=control, phase="Renommage : prévisualisation des placements",
        ):
            # A poisoned metadata field or partial Core book view must not
            # interrupt planning unrelated, independently verifiable books.
            try:
                book = view.book
                title = str(row.get("title") or getattr(book, "title", "") or "").strip()
                authors = getattr(book, "authors", ()) or ()
                author = str(row.get("author") or (authors[0] if authors else "") or "").strip()
                series = str(row.get("series") or row.get("collection") or "").strip()
                index = str(row.get("series_index") or row.get("index") or "").strip()
                placements = tuple(getattr(view, "placements", ()) or ())
            except Exception:
                skipped += 1
                continue
            if not title:
                skipped += 1
                continue
            for placement in placements:
                try:
                    if placement.kind != "library":
                        continue
                    if context.identity_id is not None and placement.identity_id != context.identity_id:
                        continue
                    placement_id = placement.placement_id
                    if (
                        type(placement_id) is not str
                        or not 0 < len(placement_id) <= 1024
                        or placement_id.strip() != placement_id
                        or any(ord(char) < 32 or ord(char) == 127 for char in placement_id)
                    ):
                        skipped += 1
                        continue
                    if placement_id in seen:
                        continue
                    seen.add(placement_id)
                    current = str(placement.filename or "").strip()
                    if not current:
                        skipped += 1
                        continue
                    extension = PurePath(current).suffix.lstrip(".") or "epub"
                except Exception:
                    skipped += 1
                    continue
                try:
                    proposed = naming.preview(
                        title=title,
                        author=author,
                        series=series,
                        index=index,
                        extension=extension,
                    )
                    # A Core naming preview is a single filename, not a
                    # file path or a stringifiable provider object. Match the
                    # read-only naming audit's strict proposal contract:
                    # an invalid preview must never enter a mutation plan.
                    if type(proposed) is not str:
                        raise ValueError("unusable naming policy proposal")
                    expected = proposed
                    if (
                        not expected or expected != expected.strip()
                        or len(expected) > 512
                        or expected in {".", ".."}
                        or "/" in expected or "\\" in expected
                        or any(ord(ch) < 32 or ord(ch) == 127 for ch in expected)
                    ):
                        raise ValueError("invalid naming policy filename")
                    matches = PurePath(current).name == expected
                except Exception:
                    # Untrusted proposed filenames may fail while converted,
                    # even when the Core preview call itself succeeded.
                    policy_failures += 1
                    continue
                if matches:
                    continue
                placement_ids.append(placement_id)
                if len(changes) < 100:
                    # The proposal and current filename can be private. Keep
                    # only an ordinal in the user-visible maintenance preview.
                    changes.append(f"Renommer le placement n°{len(placement_ids)}")

        summary = f"{len(placement_ids)} placement(s) à renormaliser selon la politique Core."
        warnings: list[str] = []
        if skipped:
            warnings.append(f"{skipped} entrée(s) non vérifiable(s) ignorée(s).")
        if policy_failures:
            warnings.append(
                f"{policy_failures} placement(s) sans proposition valide de la politique Core."
            )
        if read_failures:
            warnings.append(
                f"{len(read_failures)} artefact(s) non inspecté(s) : broker library.read en erreur."
            )
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=summary,
            changes=tuple(changes),
            warnings=tuple(warnings),
            facts={
                "placement_ids": tuple(placement_ids),
                "skipped": skipped,
                "policy_failures": policy_failures,
                "read_failures": len(read_failures),
            },
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        from cifereca.plugin_api import LibraryMutationRequest

        # Cached or caller-supplied plans can be stale or malformed. Mutation
        # identifiers must be plain opaque strings; never call str() on
        # arbitrary inputs before a Core filesystem operation.
        # Cached plans are untrusted. Never let the metadata of a
        # different tool (or a non-mapping facts payload) authorize a Core
        # filesystem mutation, even if it happens to expose placement_ids.
        facts = plan.facts
        invalid_plan = (
            type(plan.tool_id) is not str
            or plan.tool_id != self.info.id
            or type(facts) is not dict
        )
        # A wrong-shaped facts object may not implement get(). Keep the
        # invalid_plan marker, but inspect a harmless empty dictionary while
        # building the same expurgated failure result.
        if type(facts) is not dict:
            facts = {}
        raw_ids = facts.get("placement_ids", ()) if type(facts) is dict else ()
        invalid_plan = invalid_plan or type(raw_ids) not in (tuple, list)
        if not invalid_plan and len(raw_ids) > 100_000:
            invalid_plan = True
        ids: list[str] = []
        seen_ids: set[str] = set()
        if not invalid_plan:
            for value in raw_ids:
                if (
                    type(value) is not str or not 0 < len(value) <= 1024
                    or value.strip() != value
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
                    or value in seen_ids
                ):
                    invalid_plan = True
                    break
                seen_ids.add(value)
                ids.append(value)

        raw_failures = facts.get("policy_failures", 0)
        if raw_failures is None:
            preview_failures = 0
        elif type(raw_failures) is int and 0 <= raw_failures <= 1_000_000:
            preview_failures = raw_failures
        elif (
            type(raw_failures) is str and 1 <= len(raw_failures) <= 7
            and raw_failures.isascii() and raw_failures.isdecimal()
            and int(raw_failures) <= 1_000_000
        ):
            preview_failures = int(raw_failures)
        else:
            invalid_plan = True
            preview_failures = 0

        # Plan warnings can come from a stale cache or a caller. Never copy
        # arbitrary warning text (potential paths, credentials, titles) to a
        # public maintenance result. Reconstruct only the known diagnostics
        # from bounded counters produced by plan().
        plan_counts: dict[str, int] = {}
        for name in ("skipped", "read_failures"):
            count = facts.get(name, 0)
            if type(count) is not int or not 0 <= count <= 1_000_000:
                invalid_plan = True
            else:
                plan_counts[name] = count

        if invalid_plan:
            control.check_cancelled()
            control.report(message="Renormalisation impossible : plan invalide")
            control.check_cancelled()
            return MaintenanceResult(
                ok=False, changed=0,
                summary="Aucun placement renormalisé : plan invalide.",
                warnings=("Plan de renormalisation invalide : aucune mutation.",),
                facts={"planned": 0, "failures": 1, "invalid_plan": 1},
            )

        broker = self._broker("library.maintenance")
        changed = 0
        failures = 0
        total = len(ids)
        warnings: list[str] = []
        if plan_counts["skipped"]:
            warnings.append(f"{plan_counts['skipped']} entrée(s) non vérifiable(s) ignorée(s).")
        if preview_failures:
            warnings.append(
                f"{preview_failures} placement(s) sans proposition valide de la politique Core."
            )
        if plan_counts["read_failures"]:
            warnings.append(
                f"{plan_counts['read_failures']} artefact(s) non inspecté(s) : broker library.read en erreur."
            )
        control.check_cancelled()
        if not total:
            control.report(message="Aucun placement à renormaliser")
        for index, placement_id in enumerate(ids, start=1):
            control.check_cancelled()
            control.report(current=index - 1, total=total, message=f"Renormalisation {index}/{total}")
            try:
                mutation = broker.plan(LibraryMutationRequest("normalize_filename", placement_id=placement_id))
                result = broker.run(mutation, control)
                # A changed counter is evidence of completed mutations, not
                # an arbitrary provider value to coerce with int(). Refuse
                # negative, boolean, non-numeric or unbounded claims.
                raw_changed = getattr(result, "changed", None)
                raw_ok = getattr(result, "ok", None)
                if (
                    type(raw_changed) is not int or raw_changed not in (0, 1)
                    or type(raw_ok) is not bool
                ):
                    # The Core mutation response must attest *both* status
                    # and modification count; no implicit success from a
                    # partial or malformed broker result.
                    raise ValueError("unverifiable Core mutation outcome")
                if raw_ok is False:
                    failures += 1
                changed += raw_changed
                # Broker warnings may include private local paths or provider
                # responses. Public results expose only a bounded count.
                core_warnings = getattr(result, "warnings", ())
                count = (
                    len(core_warnings)
                    if isinstance(core_warnings, (tuple, list))
                    else 1 if core_warnings else 0
                )
                if count:
                    warnings.append(
                        f"Renormalisation {index}/{total} : "
                        f"{min(count, 1_000_000)} avertissement(s) du service"
                    )
            except Exception:
                # A cancellation during the Core mutation is not a failed
                # placement to swallow. Never leak exception messages, class
                # names or paths from an untrusted mutation broker.
                control.check_cancelled()
                failures += 1
                warnings.append(f"Renormalisation {index}/{total} : échec du service")
        # A cancellation during the last mutation must not emit completion.
        control.check_cancelled()
        # A skipped placement was never checked against the naming policy.
        # Do not report a fully successful run solely because the remaining
        # placements were renamed without error.
        incomplete = bool(
            failures or preview_failures
            or plan_counts["read_failures"] or plan_counts["skipped"]
        )
        if total:
            status = (
                "Renormalisation terminée"
                if not incomplete
                else (
                    f"Renormalisation partielle : {failures} échec(s), "
                    f"{preview_failures} proposition(s) indisponible(s), "
                    f"{plan_counts['read_failures']} artefact(s) non inspecté(s), "
                    f"{plan_counts['skipped']} entrée(s) ignorée(s)"
                )
            )
            control.report(current=total, total=total, message=status)
        else:
            if plan_counts["skipped"]:
                message = (
                    f"Renormalisation incomplète : "
                    f"{preview_failures} proposition(s) indisponible(s), "
                    f"{plan_counts['read_failures']} artefact(s) non inspecté(s), "
                    f"{plan_counts['skipped']} entrée(s) ignorée(s)"
                )
            else:
                # Preserve established feedback for plans containing only
                # preview policy failures or library.read failures.
                message = (
                    f"Renormalisation : {preview_failures} placement(s) sans proposition"
                    if preview_failures else (
                        f"Renormalisation incomplète : {plan_counts['read_failures']} artefact(s) non inspecté(s)"
                        if plan_counts["read_failures"] else
                        "Renormalisation terminée : aucun placement"
                    )
                )
            control.report(message=message)
        facts = {"planned": len(ids), "failures": failures}
        if preview_failures:
            facts["policy_failures"] = preview_failures
        if plan_counts["read_failures"]:
            facts["read_failures"] = plan_counts["read_failures"]
        if plan_counts["skipped"]:
            facts["skipped"] = plan_counts["skipped"]
        summary = f"{changed} placement(s) renormalisé(s)."
        if plan_counts["skipped"]:
            summary += f" {plan_counts['skipped']} entrée(s) ignorée(s)."
        return MaintenanceResult(
            ok=not incomplete,
            changed=changed,
            summary=summary,
            warnings=tuple(warnings),
            facts=facts,
        )


class DuplicateCleanupTool(_ReadOnlyTool):
    info = MaintenanceToolInfo(
        id="library.duplicates.cleanup",
        title="Nettoyer les doublons physiques",
        description="Supprime les placements bibliothèque redondants d'un même artefact SHA-256 pour une même identité.",
        icon_name="edit-delete-symbolic",
        scope="library",
        danger_level="destructive",
        supports_dry_run=True,
        cancellable=True,
    )

    def plan(self, context: MaintenanceContext) -> MaintenancePlan:
        return self._plan_scan(context, None)

    def plan_with_progress(self, context: MaintenanceContext, control) -> MaintenancePlan:
        return self._plan_scan(context, control)

    def _plan_scan(self, context: MaintenanceContext, control) -> MaintenancePlan:
        if control is not None:
            control.check_cancelled()
            control.report(message="doublons : chargement de l'inventaire")
        metadata = self._broker("metadata")
        library_read = self._broker("library.read")
        pairs: list[tuple[str, str]] = []
        changes: list[str] = []
        read_failures: list[str] = []
        seen_views: set[tuple[str, str, tuple[str, ...]]] = set()

        for _row, view in _iter_views(
            metadata, library_read, context.identity_id, read_failures,
            control=control, phase="Doublons : analyse des placements",
        ):
            try:
                # These values come from a third-party Core-backed view.
                # Invalid fields must not abort other artifacts in the plan.
                book_key = str(getattr(view.book, "book_id", "") or "")
                artifact_key = ",".join(
                    sorted(str(a.artifact_id) for a in getattr(view, "artifacts", ()))
                )
                placements = tuple(getattr(view, "placements", ()) or ())
                # Some legacy Core views expose no artifact list. Book ID
                # alone then conflates independently fetched placement sets,
                # suppressing real duplicate candidates from later views.
                # Keep a fingerprint of actual, native placement references.
                fingerprint_ids: set[str] = set()
                for placement in placements:
                    try:
                        identifier = getattr(placement, "placement_id", None)
                    except Exception:
                        # A single poisoned placement must not discard valid
                        # sibling placements or their duplicate candidates.
                        continue
                    if (
                        type(identifier) is str and 0 < len(identifier) <= 1024
                        and identifier.strip() == identifier
                        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in identifier)
                    ):
                        fingerprint_ids.add(identifier)
                placement_key = tuple(sorted(fingerprint_ids))
                key = (book_key, artifact_key, placement_key)
            except Exception:
                read_failures.append("unavailable")
                continue
            if placement_key and key in seen_views:
                continue
            if placement_key:
                seen_views.add(key)
            groups: dict[tuple[str, str | None], list[Any]] = {}
            seen_placements: set[str] = set()
            for placement in placements:
                try:
                    if placement.kind != "library":
                        continue
                    identity = placement.identity_id
                    if context.identity_id is not None and identity != context.identity_id:
                        continue
                    placement_id = placement.placement_id
                    artifact_id = placement.artifact_id
                    if (
                        type(placement_id) is not str
                        or not 0 < len(placement_id) <= 1024
                        or placement_id.strip() != placement_id
                        or any(ord(char) < 32 or ord(char) == 127 for char in placement_id)
                        or type(artifact_id) is not str
                        or not 0 < len(artifact_id) <= 1024
                        or artifact_id.strip() != artifact_id
                        or any(ord(char) < 32 or ord(char) == 127 for char in artifact_id)
                        or (identity is not None and type(identity) is not str)
                    ):
                        read_failures.append("unavailable")
                        continue
                    if placement_id in seen_placements:
                        # Repeated views of the same physical placement are
                        # not evidence of two independent duplicate files.
                        continue
                    seen_placements.add(placement_id)
                    groups.setdefault((artifact_id, identity), []).append(placement)
                except Exception:
                    read_failures.append("unavailable")
                    continue
            for (_artifact_id, _identity), placements in groups.items():
                ordered = sorted(placements, key=lambda p: p.placement_id)
                if len(ordered) < 2:
                    continue
                keep = ordered[0]
                for duplicate in ordered[1:]:
                    pairs.append((duplicate.placement_id, keep.placement_id))
                    if len(changes) < 100:
                        changes.append(f"Supprimer le doublon n°{len(pairs)}")

        # A placement may appear in overlapping Core read views. Publishing
        # the same duplicate twice makes run() reject the *entire* destructive
        # plan, including independent safe candidates. Worse, competing
        # keepers or a keeper that is deleted elsewhere make its preview
        # ambiguous. Drop those references before presenting any actions;
        # Core will still revalidate every retained delete_duplicate call.
        keepers_by_duplicate: dict[str, set[str]] = {}
        for duplicate_id, keep_id in pairs:
            keepers_by_duplicate.setdefault(duplicate_id, set()).add(keep_id)
        duplicate_ids = set(keepers_by_duplicate)
        keeper_ids = {keep for keeps in keepers_by_duplicate.values() for keep in keeps}
        ambiguous_ids = duplicate_ids & keeper_ids
        ambiguous_ids.update(
            duplicate_id for duplicate_id, keep_ids in keepers_by_duplicate.items()
            if len(keep_ids) != 1
        )
        if ambiguous_ids:
            # This is an incomplete scan/preview, not a successful cleanup.
            # Never publish the identifiers themselves in the diagnostic.
            read_failures.append("unavailable")
        safe_pairs: list[tuple[str, str]] = []
        seen_safe_duplicates: set[str] = set()
        for duplicate_id, keep_id in pairs:
            if (
                duplicate_id in ambiguous_ids or keep_id in ambiguous_ids
                or duplicate_id in seen_safe_duplicates
            ):
                continue
            seen_safe_duplicates.add(duplicate_id)
            safe_pairs.append((duplicate_id, keep_id))
        pairs = safe_pairs
        changes = [
            f"Supprimer le doublon n°{index}"
            for index in range(1, min(100, len(pairs)) + 1)
        ]

        warnings: list[str] = []
        if pairs:
            warnings.append(
                "Chaque suppression est revérifiée par Core comme doublon du même artefact."
            )
        if read_failures:
            warnings.append(
                f"{len(read_failures)} artefact(s) non inspecté(s) : broker library.read en erreur."
            )
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=f"{len(pairs)} placement(s) physique(s) redondant(s) vérifié(s).",
            changes=tuple(changes),
            warnings=tuple(warnings),
            facts={"pairs": tuple(pairs), "read_failures": len(read_failures)},
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        from cifereca.plugin_api import Conflict, LibraryMutationRequest

        # Validate the entire potentially restored plan before the destructive
        # broker is accessed; never stringify untrusted placement IDs.
        # An otherwise plausible list of placement pairs may come from a
        # restored plan for an unrelated tool or a corrupted plugin cache.
        # A destructive broker must never be reached for either case.
        facts = plan.facts
        invalid_plan = (
            type(plan.tool_id) is not str
            or plan.tool_id != self.info.id
            or type(facts) is not dict
        )
        # A wrong-shaped facts object may not implement get(). Keep the
        # invalid_plan marker, but inspect a harmless empty dictionary while
        # building the same expurgated failure result.
        if type(facts) is not dict:
            facts = {}
        raw_pairs = facts.get("pairs", ()) if type(facts) is dict else ()
        invalid_plan = invalid_plan or type(raw_pairs) not in (list, tuple)
        if not invalid_plan and len(raw_pairs) > 100_000:
            invalid_plan = True
        pairs: list[tuple[str, str]] = []
        duplicates: set[str] = set()
        keepers: set[str] = set()

        def valid_id(value: Any) -> bool:
            return (
                type(value) is str and 0 < len(value) <= 1024
                and value.strip() == value
                and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
            )

        if not invalid_plan:
            for entry in raw_pairs:
                if type(entry) not in (tuple, list) or len(entry) != 2:
                    invalid_plan = True
                    break
                duplicate_id, keep_id = entry
                if (
                    not valid_id(duplicate_id) or not valid_id(keep_id)
                    or duplicate_id == keep_id or duplicate_id in duplicates
                ):
                    invalid_plan = True
                    break
                duplicates.add(duplicate_id)
                keepers.add(keep_id)
                pairs.append((duplicate_id, keep_id))
            if duplicates.intersection(keepers):
                invalid_plan = True

        read_failures = facts.get("read_failures", 0)
        if type(read_failures) is not int or not 0 <= read_failures <= 1_000_000:
            invalid_plan = True

        if invalid_plan:
            control.check_cancelled()
            control.report(message="Nettoyage impossible : plan de doublons invalide")
            control.check_cancelled()
            return MaintenanceResult(
                ok=False, changed=0,
                summary="Aucun placement supprimé : plan de doublons invalide.",
                warnings=("Plan de doublons invalide : aucune suppression.",),
                facts={"planned": 0, "failures": 1, "invalid_plan": 1},
            )

        broker = self._broker("library.maintenance")
        changed = 0
        failures = 0
        total = len(pairs)
        # A cached or supplied plan can contain arbitrary warning text. Only
        # publish diagnostics reconstructed from this tool's known facts:
        # never pass paths, book titles or tokens from plan.warnings to GTK.
        warnings: list[str] = []
        safety_notice = "Chaque suppression est revérifiée par Core comme doublon du même artefact."
        # Preserve the actual preview notice, but not caller-supplied text.
        if pairs and type(plan.warnings) in (tuple, list) and any(
            type(value) is str and value == safety_notice
            for value in plan.warnings[:100]
        ):
            warnings.append(safety_notice)
        if read_failures:
            warnings.append(
                f"{read_failures} artefact(s) non inspecté(s) : broker library.read en erreur."
            )
        control.check_cancelled()
        if not total:
            control.report(message="Aucun doublon physique à nettoyer")
        for index, (duplicate_id, keep_id) in enumerate(pairs, start=1):
            control.check_cancelled()
            control.report(current=index - 1, total=total, message=f"Doublon {index}/{total}")
            try:
                legacy_request = LibraryMutationRequest(
                    "delete_duplicate",
                    placement_id=duplicate_id,
                    keep_placement_id=keep_id,
                )
                try:
                    mutation = broker.plan(legacy_request)
                except Conflict as exc:
                    # This tool's run() is reached after the destructive
                    # confirmation dialog in the Maintenance host. A B
                    # placement cannot use the historical DB-first deleter.
                    # Only Core's *exact* supervised-required signal opts in
                    # to its new confirmed, locked and recoverable engine.
                    # Never infer canonical status from view metadata/SHA.
                    if getattr(exc, "code", "") != "canonical_dedup_supervised_required":
                        raise
                    mutation = broker.plan(LibraryMutationRequest(
                        "canonical_dedup",
                        placement_id=duplicate_id,
                        keep_placement_id=keep_id,
                        options={"action": "commit", "confirmed": True},
                    ))
                result = broker.run(mutation, control)
                # A changed counter is evidence of completed mutations, not
                # an arbitrary provider value to coerce with int(). Refuse
                # negative, boolean, non-numeric or unbounded claims.
                raw_changed = getattr(result, "changed", None)
                raw_ok = getattr(result, "ok", None)
                if (
                    type(raw_changed) is not int or raw_changed not in (0, 1)
                    or type(raw_ok) is not bool
                ):
                    # A missing/non-boolean outcome must not default to
                    # success for a destructive operation. Core could have
                    # partially changed files: only attested counts are shown.
                    raise ValueError("unverifiable Core mutation outcome")
                if raw_ok is False:
                    failures += 1
                changed += raw_changed
                # The Core mutation may return a private filesystem path,
                # bearer token or provider response in warning text. The
                # maintenance line and result must export counts only.
                core_warnings = getattr(result, "warnings", ())
                count = (
                    len(core_warnings)
                    if isinstance(core_warnings, (tuple, list))
                    else 1 if core_warnings else 0
                )
                if count:
                    warnings.append(
                        f"Doublon {index}/{total} : {min(count, 1_000_000)} avertissement(s) du service"
                    )
            except Exception:
                # A cancelled destructive mutation must stop the batch. Never
                # expose placement IDs, exception messages *or exception class
                # names* (third-party brokers may generate private class names).
                control.check_cancelled()
                failures += 1
                warnings.append(f"Doublon {index}/{total} : échec du service")
        # Cancellation during the final mutation must not report completion.
        control.check_cancelled()
        if total:
            status = (
                "Nettoyage terminé" if not failures and not read_failures
                else f"Nettoyage partiel : {failures} échec(s), {read_failures} artefact(s) non inspecté(s)"
            )
            control.report(current=total, total=total, message=status)
        else:
            control.report(message=(
                f"Nettoyage incomplet : {read_failures} artefact(s) non inspecté(s)"
                if read_failures else "Nettoyage terminé : aucun doublon"
            ))
        facts = {"planned": len(pairs), "failures": failures}
        if read_failures:
            facts["read_failures"] = read_failures
        return MaintenanceResult(
            ok=failures == 0 and read_failures == 0,
            changed=changed,
            summary=f"{changed} placement(s) redondant(s) supprimé(s).",
            warnings=tuple(warnings),
            facts=facts,
        )


NAMING_REPAIR = NamingRepairTool()
DUPLICATE_CLEANUP = DuplicateCleanupTool()
