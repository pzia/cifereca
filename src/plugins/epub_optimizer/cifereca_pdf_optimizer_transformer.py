from __future__ import annotations

from pathlib import Path
from typing import Any

from cifereca.plugin_api import ArtifactFormat, TransformCapability, TransformPlan, TransformResult
from cifereca_epub_optimizer.profiles import DeviceProfile, discover_profiles

PDF = ArtifactFormat(media_type="application/pdf", format="pdf", drm="none")
CAPABILITY_ID = "pdf.image-optimize-eink"


class _ControlEvent:
    def __init__(self, control: Any) -> None:
        self.control = control

    def is_set(self) -> bool:
        return bool(self.control.cancelled())


class PdfOptimizerTransformer:
    def __init__(self) -> None:
        self.context = None

    def activate(self, context):
        self.context = context
        context.report_diagnostic(
            "PDF e-ink optimizer transformer activated",
            "ok",
            detail={"category": "document.transformer"},
            level="detailed",
        )
        return self

    def deactivate(self) -> None:
        self.context = None

    def capabilities(self):
        return (
            TransformCapability(
                id=CAPABILITY_ID,
                input_formats=(PDF,),
                output_formats=(PDF,),
                preserves_original=True,
                supports_profiles=True,
                supports_plan=True,
                supports_batch=False,
                description="Conserve texte/vectoriel et optimise uniquement les images raster compatibles d'un PDF.",
            ),
        )

    def supports(self, request) -> bool:
        return str(getattr(request, "capability_id", "")) == CAPABILITY_ID

    def _require_context(self):
        if self.context is None:
            raise RuntimeError("PDF optimizer transformer is not activated")
        return self.context

    def _profile(self, request) -> tuple[str, DeviceProfile]:
        context = self._require_context()
        profiles = discover_profiles(context)
        if not profiles:
            raise RuntimeError("aucun profil device.profile actif")
        requested = str(request.profile_plugin_id or "").strip()
        if requested:
            if requested in profiles:
                return requested, profiles[requested]
            for plugin_id, profile in profiles.items():
                if profile.id == requested:
                    return plugin_id, profile
            raise ValueError(f"profil device.profile inconnu: {requested}")
        configured = str(getattr(context, "config", {}).get("default_profile", "") or "").strip()
        if configured:
            for plugin_id, profile in profiles.items():
                if plugin_id == configured or profile.id == configured:
                    return plugin_id, profile
        return next(iter(profiles.items()))

    @staticmethod
    def _validate_request(request, source) -> None:
        if request.capability_id != CAPABILITY_ID:
            raise ValueError(f"capability inconnue: {request.capability_id}")
        fmt = getattr(getattr(source, "artifact", None), "format", None)
        if fmt is not None and str(getattr(fmt, "format", "")).casefold() not in {"", "pdf"}:
            raise ValueError("la source doit être un PDF")
        if Path(source.path).suffix.casefold() != ".pdf":
            raise ValueError("la source doit être un PDF")

    def _limits(self, request) -> tuple[int, int, int]:
        context = self._require_context()
        config = getattr(context, "config", {})
        return (
            int(request.options.get("max_pdf_pages") or config.get("max_pdf_pages") or 5000),
            int(request.options.get("max_image_pixels") or config.get("max_image_pixels") or 64_000_000),
            int(request.options.get("max_image_mb") or config.get("max_image_mb") or 64),
        )

    def _cpu_workers(self, request) -> int:
        config = getattr(self._require_context(), "config", {})
        value = request.options.get("cpu_workers", config.get("cpu_workers", 1))
        if type(value) is not int or value not in (1, 2, 4):
            raise ValueError("cpu_workers doit valoir 1, 2 ou 4")
        return value

    @staticmethod
    def _output_name(source_path: str | Path, profile: DeviceProfile) -> str:
        source = Path(source_path)
        return f"{source.stem} [{profile.suffix}].pdf"

    def plan(self, request, source, control=None):
        self._validate_request(request, source)
        if control is not None:
            control.check_cancelled()
        profile_plugin_id, profile = self._profile(request)
        max_pages, max_image_pixels, max_image_mb = self._limits(request)
        cpu_workers = self._cpu_workers(request)
        from cifereca_epub_optimizer.pdf_engine import inspect_pdf

        inspection = inspect_pdf(
            source.path,
            profile=profile,
            max_pages=max_pages,
            max_image_pixels=max_image_pixels,
            max_image_mb=max_image_mb,
            cancel_event=_ControlEvent(control) if control is not None else None,
        )
        warnings: list[str] = []
        if profile.max_zoom_factor is None:
            warnings.append(
                "Zoom PDF du profil inconnu : aucune réduction liée à la taille d'affichage n'est appliquée."
            )
        if inspection.preserved_images:
            warnings.append(f"{inspection.preserved_images} image(s) seront conservée(s) sans modification.")
        return TransformPlan(
            output_format=PDF,
            proposed_filename=self._output_name(source.path, profile),
            warnings=tuple(warnings),
            facts={
                "profile_plugin_id": profile_plugin_id,
                "profile_id": profile.id,
                "pages": inspection.pages,
                "images_seen": inspection.images_seen,
                "candidate_images": inspection.candidate_images,
                "preserved_images": inspection.preserved_images,
                "raster_bytes": inspection.raster_bytes,
                "estimated_reduction_percent": round(inspection.estimated_reduction_percent, 1),
                "preserves_text_vector": True,
                "cpu_workers": cpu_workers,
            },
            estimated_size_bytes=inspection.estimated_size_bytes,
        )

    def transform(self, request, source, destination, control):
        self._validate_request(request, source)
        control.check_cancelled()
        context = self._require_context()
        profile_plugin_id, profile = self._profile(request)
        max_pages, max_image_pixels, max_image_mb = self._limits(request)
        cpu_workers = self._cpu_workers(request)

        def progress(percent: int, message: str) -> None:
            control.check_cancelled()
            control.report(current=percent, total=100, message=message)

        from cifereca_epub_optimizer.engine import CancelledError
        from cifereca_epub_optimizer.pdf_engine import optimize_pdf

        try:
            report = optimize_pdf(
                source.path,
                destination.path,
                profile=profile,
                max_pages=max_pages,
                max_image_pixels=max_image_pixels,
                max_image_mb=max_image_mb,
                progress=progress,
                cancel_event=_ControlEvent(control),
                cpu_workers=cpu_workers,
            )
        except CancelledError:
            control.check_cancelled()
            raise
        except Exception as exc:
            context.report_diagnostic(
                "PDF optimization failed",
                "error",
                detail={"error_type": type(exc).__name__, "profile_plugin_id": profile_plugin_id},
                level="normal",
            )
            raise

        warnings = tuple((report.image_errors + report.image_notes)[:20])
        facts = {
            "profile_plugin_id": profile_plugin_id,
            "profile_id": profile.id,
            "pages": report.pages,
            "source_bytes": report.source_bytes,
            "output_bytes": report.output_bytes,
            "bytes_saved": report.bytes_saved,
            "reduction_percent": report.reduction_percent,
            "images_seen": report.images_seen,
            "images_optimized": report.images_optimized,
            "images_kept": report.images_kept,
            "preserves_text_vector": True,
            "cpu_workers": cpu_workers,
        }
        context.report_diagnostic(
            "PDF optimization completed",
            "ok",
            detail={
                "profile_plugin_id": profile_plugin_id,
                "images_seen": report.images_seen,
                "images_optimized": report.images_optimized,
                "output_bytes": report.output_bytes,
            },
            level="detailed",
        )
        return TransformResult(
            output_format=PDF,
            proposed_filename=self._output_name(source.path, profile),
            sha256=report.output_sha256,
            size_bytes=report.output_bytes,
            facts=facts,
            warnings=warnings,
        )


PLUGIN = PdfOptimizerTransformer()
