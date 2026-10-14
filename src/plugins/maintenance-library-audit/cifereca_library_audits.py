from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePath
from typing import Any
import hashlib
import zipfile
from urllib.parse import urlsplit

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


def _language_values(row: Mapping[str, Any]) -> list[str]:
    meta = _metadata(row)
    values: list[str] = []
    for container in (row, meta):
        for key in ("language", "languages", "lang", "langs"):
            value = container.get(key)
            if isinstance(value, str):
                if value.strip():
                    values.append(value.strip())
            elif isinstance(value, (list, tuple, set)):
                values.extend(str(item).strip() for item in value if str(item).strip())
    return list(dict.fromkeys(values))


def _file_integrity_issues(path: str | Path, media_format: str = "", *, expected_size: int = 0, expected_sha256: str = "") -> tuple[str, ...]:
    target=Path(path)
    issues=[]
    if not target.exists():
        return ("missing",)
    if target.is_symlink():
        issues.append("symlink")
    if not target.is_file():
        return tuple(issues + ["not_file"])
    try:
        stat=target.stat()
    except OSError:
        return tuple(issues + ["unreadable"])
    if expected_size and stat.st_size != int(expected_size):
        issues.append("size_mismatch")
    digest=""
    if expected_sha256:
        try:
            h=hashlib.sha256()
            with target.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b""):
                    h.update(chunk)
            digest=h.hexdigest()
            if digest.casefold() != str(expected_sha256).removeprefix("sha256:").casefold():
                issues.append("sha256_mismatch")
        except OSError:
            issues.append("unreadable")
    fmt=str(media_format or target.suffix.lstrip(".")).casefold()
    try:
        if fmt in {"epub","kepub"} or target.name.casefold().endswith((".epub",".kepub.epub")):
            with zipfile.ZipFile(target) as archive:
                if archive.testzip() is not None:
                    issues.append("zip_crc")
                try:
                    if archive.read("mimetype") != b"application/epub+zip":
                        issues.append("epub_mimetype")
                    archive.getinfo("META-INF/container.xml")
                except KeyError:
                    issues.append("epub_structure")
        elif fmt=="pdf" or target.suffix.casefold()==".pdf":
            with target.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    issues.append("pdf_header")
    except (OSError, zipfile.BadZipFile):
        issues.append("container_invalid")
    return tuple(dict.fromkeys(issues))


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
        entries = _rows(self._broker("catalog").catalog_entries(identity_id=context.identity_id))
        missing = 0
        invalid = 0
        samples: list[str] = []
        for notice_index, row in enumerate(entries, start=1):
            cover = _cover_value(row)
            # Preview warnings cross the UI/diagnostic boundary. Never embed
            # free-form titles, local paths or untrusted cover URLs there.
            if not cover:
                missing += 1
                if len(samples) < 12:
                    samples.append(f"Sans couverture : notice n°{notice_index}")
                continue
            try:
                parsed = urlsplit(cover)
                suspicious = bool(
                    parsed.scheme and parsed.scheme not in {"http", "https", "file"}
                )
            except ValueError:
                # Malformed bracketed IPv6 hosts and other invalid URL shapes
                # are rejected per row instead of aborting the entire scan.
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
        entries = _rows(self._broker("catalog").catalog_entries(identity_id=context.identity_id))
        seen = 0
        invalid = 0
        noncanonical = 0
        samples: list[str] = []
        for notice_index, row in enumerate(entries, start=1):
            # Tool plans and results can be exported to diagnostics/UI. Neither
            # free-form book titles nor untrusted raw language tags are safe
            # diagnostic labels (both may contain credentials or private paths).
            # Keep only the deterministic catalogue ordinal and issue class.
            for raw in _language_values(row):
                seen += 1
                canonical = normalize_language_tag(raw)
                if canonical is None:
                    invalid += 1
                    if len(samples) < 12:
                        samples.append(f"Langue invalide : notice n°{notice_index}")
                elif canonical != raw:
                    noncanonical += 1
                    if len(samples) < 12:
                        samples.append(f"Langue non canonique : notice n°{notice_index}")
        summary = f"{seen} valeur(s) de langue ; {invalid} invalide(s), {noncanonical} non canonique(s)."
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=summary,
            warnings=tuple(samples),
            facts={"language_values": seen, "invalid": invalid, "noncanonical": noncanonical},
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
        entries = _rows(self._broker("metadata").library_entries(identity_id=context.identity_id))
        naming = self._broker("library.naming")
        checked = 0
        mismatch = 0
        unavailable = 0
        samples: list[str] = []
        for row in entries:
            title = str(row.get("title") or "").strip()
            current = str(row.get("filename") or row.get("file_name") or "").strip()
            if not current:
                path = str(row.get("path") or "").strip()
                current = PurePath(path).name if path else ""
            if not title or not current:
                unavailable += 1
                continue
            extension = PurePath(current).suffix.lstrip(".") or str(row.get("format") or "epub").lower()
            expected = naming.preview(
                title=title,
                author=str(row.get("author") or ""),
                series=str(row.get("series") or row.get("collection") or ""),
                index=str(row.get("series_index") or row.get("index") or ""),
                extension=extension,
            )
            checked += 1
            if PurePath(current).name != PurePath(str(expected)).name:
                mismatch += 1
                if len(samples) < 12:
                    samples.append(f"{PurePath(current).name} → {PurePath(str(expected)).name}")
        summary = f"{mismatch} nom(s) différent(s) de la politique Core sur {checked} fichier(s) vérifiable(s)."
        warnings = list(samples)
        if unavailable:
            warnings.append(f"{unavailable} entrée(s) sans nom/titre public vérifiable.")
        warnings.append("Audit uniquement : aucune mutation de placement n'est effectuée.")
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=summary,
            warnings=tuple(warnings),
            facts={"checked": checked, "mismatch": mismatch, "unavailable": unavailable},
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
        metadata=self._broker("metadata")
        library_read=self._broker("library.read")
        candidates=[]
        missing_path=0
        read_failures=0
        for row in _rows(metadata.library_entries(identity_id=context.identity_id)):
            if not row.get("local"):
                continue
            path=str(row.get("local_path") or "").strip()
            artifact_id=str(row.get("artifact_id") or "").strip()
            if not path:
                missing_path += 1
                continue
            artifact=None
            if artifact_id:
                try:
                    artifact=library_read.artifact(artifact_id)
                except Exception:
                    read_failures += 1
            candidates.append({
                "path": path,
                "title": str(row.get("title") or PurePath(path).name),
                "format": str(row.get("media_format") or getattr(artifact,"format","") or ""),
                "expected_size": int(getattr(artifact,"size_bytes",0) or 0),
                "expected_sha256": str(getattr(artifact,"sha256","") or row.get("sha256") or ""),
            })
        warnings=[]
        if missing_path:
            warnings.append(f"{missing_path} entrée(s) locale(s) sans chemin observable.")
        if read_failures:
            warnings.append(
                f"{read_failures} artefact(s) sans métadonnées de référence : broker library.read en erreur."
            )
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=f"{len(candidates)} fichier(s) local(aux) à vérifier.",
            warnings=tuple(warnings),
            facts={
                "candidates": tuple(candidates),
                "missing_path": missing_path,
                "read_failures": read_failures,
            },
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        candidates=tuple(plan.facts.get("candidates",()))
        total=len(candidates)
        warnings=list(plan.warnings)
        read_failures=int(plan.facts.get("read_failures") or 0)
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
            issues=_file_integrity_issues(
                row.get("path",""), row.get("format",""),
                expected_size=int(row.get("expected_size") or 0),
                expected_sha256=str(row.get("expected_sha256") or ""),
            )
            if issues:
                invalid += 1
                if "missing" in issues: missing += 1
                for issue in issues: by_issue[issue]=by_issue.get(issue,0)+1
                if len(warnings)<100:
                    warnings.append(f"{row.get('title') or PurePath(str(row.get('path') or '')).name}: {', '.join(issues)}")
        # A cancellation received while the last file was being inspected
        # must not publish a misleading completed/100% state.
        control.check_cancelled()
        if total:
            control.report(current=total,total=total,message="Audit d’intégrité terminé")
        else:
            control.report(message="Audit d’intégrité terminé : aucun fichier")
        return MaintenanceResult(
            ok=invalid==0 and read_failures==0,
            changed=0,
            summary=f"{invalid} fichier(s) en anomalie sur {len(candidates)} vérifié(s), dont {missing} manquant(s).",
            warnings=tuple(warnings),
            facts={
                "checked":len(candidates),
                "invalid":invalid,
                "missing":missing,
                "read_failures":read_failures,
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
    return str(row.get("artifact_id") or row.get("artifact") or "").strip()


def _iter_views(metadata, library_read, identity_id: str | None, failures: list[str] | None = None):
    seen: set[str] = set()
    for row in _rows(metadata.library_entries(identity_id=identity_id)):
        artifact_id = _artifact_id(row)
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        try:
            view = library_read.for_artifact(artifact_id)
        except Exception as exc:
            if failures is not None:
                failures.append(type(exc).__name__)
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
        metadata = self._broker("metadata")
        naming = self._broker("library.naming")
        library_read = self._broker("library.read")
        placement_ids: list[str] = []
        changes: list[str] = []
        skipped = 0
        read_failures: list[str] = []
        seen: set[str] = set()

        for row, view in _iter_views(metadata, library_read, context.identity_id, read_failures):
            title = str(row.get("title") or getattr(view.book, "title", "") or "").strip()
            authors = getattr(view.book, "authors", ()) or ()
            author = str(row.get("author") or (authors[0] if authors else "") or "").strip()
            series = str(row.get("series") or row.get("collection") or "").strip()
            index = str(row.get("series_index") or row.get("index") or "").strip()
            if not title:
                skipped += 1
                continue
            for placement in getattr(view, "placements", ()):
                if placement.kind != "library":
                    continue
                if context.identity_id is not None and placement.identity_id != context.identity_id:
                    continue
                if placement.placement_id in seen:
                    continue
                seen.add(placement.placement_id)
                current = str(placement.filename or "").strip()
                if not current:
                    skipped += 1
                    continue
                extension = PurePath(current).suffix.lstrip(".") or "epub"
                expected = str(naming.preview(
                    title=title,
                    author=author,
                    series=series,
                    index=index,
                    extension=extension,
                ))
                if PurePath(current).name == PurePath(expected).name:
                    continue
                placement_ids.append(placement.placement_id)
                if len(changes) < 100:
                    changes.append(f"{PurePath(current).name} → {PurePath(expected).name}")

        summary = f"{len(placement_ids)} placement(s) à renormaliser selon la politique Core."
        warnings: list[str] = []
        if skipped:
            warnings.append(f"{skipped} entrée(s) non vérifiable(s) ignorée(s).")
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
                "read_failures": len(read_failures),
            },
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        from cifereca.plugin_api import LibraryMutationRequest

        broker = self._broker("library.maintenance")
        ids = tuple(str(x) for x in plan.facts.get("placement_ids", ()) if str(x))
        changed = 0
        failures = 0
        total = len(ids)
        warnings: list[str] = list(plan.warnings)
        control.check_cancelled()
        if not total:
            control.report(message="Aucun placement à renormaliser")
        for index, placement_id in enumerate(ids, start=1):
            control.check_cancelled()
            control.report(current=index - 1, total=total, message=f"Renormalisation {index}/{total}")
            try:
                mutation = broker.plan(LibraryMutationRequest("normalize_filename", placement_id=placement_id))
                result = broker.run(mutation, control)
                if getattr(result, "ok", True) is False:
                    failures += 1
                changed += int(getattr(result, "changed", 0) or 0)
                warnings.extend(str(x) for x in getattr(result, "warnings", ()) if str(x))
            except Exception as exc:
                # A cancellation during the Core mutation is not a failed
                # placement to swallow. Never leak an exception's private path.
                control.check_cancelled()
                failures += 1
                warnings.append(f"Renormalisation {index}/{total} : {type(exc).__name__}")
        # A cancellation during the last mutation must not emit completion.
        control.check_cancelled()
        if total:
            status = "Renormalisation terminée" if not failures else f"Renormalisation : {failures} échec(s)"
            control.report(current=total, total=total, message=status)
        else:
            control.report(message="Renormalisation terminée : aucun placement")
        return MaintenanceResult(
            ok=failures == 0,
            changed=changed,
            summary=f"{changed} placement(s) renormalisé(s).",
            warnings=tuple(warnings),
            facts={"planned": len(ids), "failures": failures},
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
        metadata = self._broker("metadata")
        library_read = self._broker("library.read")
        pairs: list[tuple[str, str]] = []
        changes: list[str] = []
        read_failures: list[str] = []
        seen_views: set[str] = set()

        for _row, view in _iter_views(metadata, library_read, context.identity_id, read_failures):
            key = str(getattr(view.book, "book_id", "") or "") + ":" + ",".join(
                sorted(str(a.artifact_id) for a in getattr(view, "artifacts", ()))
            )
            if key in seen_views:
                continue
            seen_views.add(key)
            groups: dict[tuple[str, str | None], list[Any]] = {}
            for placement in getattr(view, "placements", ()):
                if placement.kind != "library":
                    continue
                if context.identity_id is not None and placement.identity_id != context.identity_id:
                    continue
                groups.setdefault((placement.artifact_id, placement.identity_id), []).append(placement)
            for (artifact_id, _identity), placements in groups.items():
                ordered = sorted(placements, key=lambda p: p.placement_id)
                if len(ordered) < 2:
                    continue
                keep = ordered[0]
                for duplicate in ordered[1:]:
                    pairs.append((duplicate.placement_id, keep.placement_id))
                    if len(changes) < 100:
                        changes.append(
                            f"Supprimer {duplicate.filename or duplicate.placement_id} "
                            f"(même artefact {artifact_id[:24]}…)"
                        )

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
        from cifereca.plugin_api import LibraryMutationRequest

        broker = self._broker("library.maintenance")
        pairs = tuple((str(a), str(b)) for a, b in plan.facts.get("pairs", ()) if str(a) and str(b))
        changed = 0
        failures = 0
        total = len(pairs)
        warnings: list[str] = list(plan.warnings)
        control.check_cancelled()
        if not total:
            control.report(message="Aucun doublon physique à nettoyer")
        for index, (duplicate_id, keep_id) in enumerate(pairs, start=1):
            control.check_cancelled()
            control.report(current=index - 1, total=total, message=f"Doublon {index}/{total}")
            try:
                mutation = broker.plan(LibraryMutationRequest(
                    "delete_duplicate",
                    placement_id=duplicate_id,
                    keep_placement_id=keep_id,
                ))
                result = broker.run(mutation, control)
                if getattr(result, "ok", True) is False:
                    failures += 1
                changed += int(getattr(result, "changed", 0) or 0)
                warnings.extend(str(x) for x in getattr(result, "warnings", ()) if str(x))
            except Exception as exc:
                # A cancelled destructive mutation must stop the batch. Never
                # expose placement IDs or the exception's private path.
                control.check_cancelled()
                failures += 1
                warnings.append(f"Doublon {index}/{total} : {type(exc).__name__}")
        # Cancellation during the final mutation must not report completion.
        control.check_cancelled()
        if total:
            status = "Nettoyage terminé" if not failures else f"Nettoyage : {failures} échec(s)"
            control.report(current=total, total=total, message=status)
        else:
            control.report(message="Nettoyage terminé : aucun doublon")
        return MaintenanceResult(
            ok=failures == 0,
            changed=changed,
            summary=f"{changed} placement(s) redondant(s) supprimé(s).",
            warnings=tuple(warnings),
            facts={"planned": len(pairs), "failures": failures},
        )


NAMING_REPAIR = NamingRepairTool()
DUPLICATE_CLEANUP = DuplicateCleanupTool()
