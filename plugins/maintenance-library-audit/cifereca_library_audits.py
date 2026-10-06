from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import PurePath
from typing import Any
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
        control.check_cancelled()
        control.report(current=1, total=1, message=plan.summary)
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
        for row in entries:
            cover = _cover_value(row)
            title = str(row.get("title") or row.get("name") or row.get("id") or "notice")
            if not cover:
                missing += 1
                if len(samples) < 12:
                    samples.append(f"Sans couverture : {title[:100]}")
                continue
            parsed = urlsplit(cover)
            if parsed.scheme and parsed.scheme not in {"http", "https", "file"}:
                invalid += 1
                if len(samples) < 12:
                    samples.append(f"Couverture non reconnue : {title[:100]}")
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
        for row in entries:
            title = str(row.get("title") or row.get("name") or row.get("id") or "notice")
            for raw in _language_values(row):
                seen += 1
                canonical = normalize_language_tag(raw)
                if canonical is None:
                    invalid += 1
                    if len(samples) < 12:
                        samples.append(f"Langue invalide {raw!r} : {title[:80]}")
                elif canonical != raw:
                    noncanonical += 1
                    if len(samples) < 12:
                        samples.append(f"{raw!r} → {canonical!r} : {title[:80]}")
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


COVER_AUDIT = CoverAuditTool()
LANGUAGE_AUDIT = LanguageAuditTool()
NAMING_AUDIT = NamingAuditTool()

# Core041 repair tools -------------------------------------------------------
# These tools never mutate the filesystem or storage directly. They only
# describe work and delegate each authorized mutation to library.maintenance.


def _artifact_id(row: Mapping[str, Any]) -> str:
    return str(row.get("artifact_id") or row.get("artifact") or "").strip()


def _iter_views(metadata, library_read, identity_id: str | None):
    seen: set[str] = set()
    for row in _rows(metadata.library_entries(identity_id=identity_id)):
        artifact_id = _artifact_id(row)
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        try:
            view = library_read.for_artifact(artifact_id)
        except Exception:
            view = None
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
        seen: set[str] = set()

        for row, view in _iter_views(metadata, library_read, context.identity_id):
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
        warnings = (f"{skipped} entrée(s) non vérifiable(s) ignorée(s).",) if skipped else ()
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=summary,
            changes=tuple(changes),
            warnings=warnings,
            facts={"placement_ids": tuple(placement_ids), "skipped": skipped},
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        from cifereca.plugin_api import LibraryMutationRequest

        broker = self._broker("library.maintenance")
        ids = tuple(str(x) for x in plan.facts.get("placement_ids", ()) if str(x))
        changed = 0
        warnings: list[str] = list(plan.warnings)
        for index, placement_id in enumerate(ids, start=1):
            control.check_cancelled()
            control.report(current=index - 1, total=len(ids), message=f"Renormalisation {index}/{len(ids)}")
            try:
                mutation = broker.plan(LibraryMutationRequest("normalize_filename", placement_id=placement_id))
                result = broker.run(mutation, control)
                changed += int(getattr(result, "changed", 0) or 0)
                warnings.extend(str(x) for x in getattr(result, "warnings", ()) if str(x))
            except Exception as exc:
                warnings.append(f"{placement_id}: {type(exc).__name__}: {exc}")
        control.report(current=len(ids), total=len(ids) or 1, message="Renormalisation terminée")
        return MaintenanceResult(
            ok=not any(": " in w and "Error" in w for w in warnings),
            changed=changed,
            summary=f"{changed} placement(s) renormalisé(s).",
            warnings=tuple(warnings),
            facts={"planned": len(ids)},
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
        seen_views: set[str] = set()

        for _row, view in _iter_views(metadata, library_read, context.identity_id):
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

        return MaintenancePlan(
            tool_id=self.info.id,
            summary=f"{len(pairs)} placement(s) physique(s) redondant(s) vérifié(s).",
            changes=tuple(changes),
            warnings=("Chaque suppression est revérifiée par Core comme doublon du même artefact.",) if pairs else (),
            facts={"pairs": tuple(pairs)},
        )

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        from cifereca.plugin_api import LibraryMutationRequest

        broker = self._broker("library.maintenance")
        pairs = tuple((str(a), str(b)) for a, b in plan.facts.get("pairs", ()) if str(a) and str(b))
        changed = 0
        warnings: list[str] = list(plan.warnings)
        for index, (duplicate_id, keep_id) in enumerate(pairs, start=1):
            control.check_cancelled()
            control.report(current=index - 1, total=len(pairs), message=f"Doublon {index}/{len(pairs)}")
            try:
                mutation = broker.plan(LibraryMutationRequest(
                    "delete_duplicate",
                    placement_id=duplicate_id,
                    keep_placement_id=keep_id,
                ))
                result = broker.run(mutation, control)
                changed += int(getattr(result, "changed", 0) or 0)
                warnings.extend(str(x) for x in getattr(result, "warnings", ()) if str(x))
            except Exception as exc:
                warnings.append(f"{duplicate_id}: {type(exc).__name__}: {exc}")
        control.report(current=len(pairs), total=len(pairs) or 1, message="Nettoyage terminé")
        return MaintenanceResult(
            ok=True,
            changed=changed,
            summary=f"{changed} placement(s) redondant(s) supprimé(s).",
            warnings=tuple(warnings),
            facts={"planned": len(pairs)},
        )


NAMING_REPAIR = NamingRepairTool()
DUPLICATE_CLEANUP = DuplicateCleanupTool()
