from __future__ import annotations

from pathlib import Path
from typing import Any

from cifereca.plugin_api import (
    ArtifactFormat,
    TransformCapability,
    TransformPlan,
    TransformResult,
)

from cifereca_epub_optimizer.profiles import DeviceProfile, default_output_path, discover_profiles


EPUB = ArtifactFormat(media_type="application/epub+zip", format="epub", drm="none")
CAPABILITY_ID = "epub.image-optimize-eink"


class _ControlEvent:
    """Adapter from public TaskControl to the engine's minimal event contract."""

    def __init__(self, control: Any) -> None:
        self.control = control

    def is_set(self) -> bool:
        return bool(self.control.cancelled())


class EpubOptimizerTransformer:
    def __init__(self) -> None:
        self.context = None

    def activate(self, context):
        self.context = context
        context.report_diagnostic(
            "EPUB Optimizer transformer activated",
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
                input_formats=(EPUB,),
                output_formats=(EPUB,),
                preserves_original=True,
                supports_profiles=True,
                supports_plan=True,
                supports_batch=False,
                description="Optimise les images raster d'un EPUB pour un profil e-ink.",
            ),
        )

    def _require_context(self):
        if self.context is None:
            raise RuntimeError("EPUB Optimizer transformer is not activated")
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
            # Transitional convenience for the former short profile ids. The
            # transformer still discovers all profiles dynamically.
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
        if fmt is not None and str(getattr(fmt, "format", "")).casefold() not in {"", "epub"}:
            raise ValueError("la source doit être un EPUB")
        if Path(source.path).suffix.casefold() != ".epub":
            raise ValueError("la source doit être un EPUB")

    def plan(self, request, source, control=None):
        self._validate_request(request, source)
        if control is not None:
            control.check_cancelled()
        profile_plugin_id, profile = self._profile(request)
        proposed = default_output_path(Path(source.path).name, profile).name
        context = self._require_context()
        max_uncompressed_mb = int(request.options.get("max_uncompressed_mb") or context.config.get("max_uncompressed_mb") or 2048)
        max_image_mb = int(request.options.get("max_image_mb") or context.config.get("max_image_mb") or 64)
        max_image_pixels = int(request.options.get("max_image_pixels") or context.config.get("max_image_pixels") or 64_000_000)
        from cifereca_epub_optimizer.engine import analyze_epub
        analysis = analyze_epub(
            source.path,
            profile=profile,
            max_uncompressed_mb=max_uncompressed_mb,
            max_image_mb=max_image_mb,
            max_image_pixels=max_image_pixels,
        )
        return TransformPlan(
            output_format=EPUB,
            proposed_filename=proposed,
            warnings=(),
            facts={
                "profile_plugin_id": profile_plugin_id,
                "profile_id": profile.id,
                "profile_width": profile.width,
                "profile_height": profile.height,
                "max_zoom_factor": profile.max_zoom_factor,
                "allow_landscape": profile.allow_landscape,
                "grayscale": profile.grayscale,
                "images_seen": analysis.images_seen,
                "images_candidates": analysis.images_candidates,
                "images_semantic_preserved": analysis.images_semantic_preserved,
                "images_over_limit": analysis.images_over_limit,
                "layout_hints": analysis.layout_hints,
            },
            estimated_size_bytes=analysis.estimated_size_bytes,
        )

    def transform(self, request, source, destination, control):
        self._validate_request(request, source)
        control.check_cancelled()
        context = self._require_context()
        profile_plugin_id, profile = self._profile(request)
        max_uncompressed_mb = int(request.options.get("max_uncompressed_mb") or context.config.get("max_uncompressed_mb") or 2048)
        max_image_mb = int(request.options.get("max_image_mb") or context.config.get("max_image_mb") or 64)
        max_image_pixels = int(request.options.get("max_image_pixels") or context.config.get("max_image_pixels") or 64_000_000)
        proposed = default_output_path(Path(source.path).name, profile).name
        # Explicit opt-in; default keeps the historical sequential engine.
        cpu_workers = int(
            request.options.get("cpu_workers")
            or context.config.get("cpu_workers") or 1
        )
        if cpu_workers not in (1, 2, 4):
            raise ValueError("cpu_workers doit valoir 1, 2 ou 4")

        def progress(percent: int, message: str) -> None:
            control.check_cancelled()
            control.report(current=percent, total=100, message=message)

        # Pillow is an install-time Python dependency managed by Core.
        # Keep the import lazy so descriptor discovery/profile activation never
        # collapses into activation_failed when the environment is incomplete.
        from cifereca_epub_optimizer.engine import CancelledError, optimize_epub

        try:
            report = optimize_epub(
                source.path,
                destination.path,
                profile=profile,
                max_uncompressed_mb=max_uncompressed_mb,
                max_image_mb=max_image_mb,
                max_image_pixels=max_image_pixels,
                progress=progress,
                cancel_event=_ControlEvent(control),
                cpu_workers=cpu_workers,
            )
        except CancelledError:
            control.check_cancelled()
            raise
        except Exception as exc:
            context.report_diagnostic(
                "EPUB optimization failed",
                "error",
                detail={"error_type": type(exc).__name__, "profile_plugin_id": profile_plugin_id},
                level="normal",
            )
            raise

        warnings = tuple(f"image conservée: {item.split(':',1)[0]}" for item in report.image_errors[:20])
        facts = {
            "profile_plugin_id": profile_plugin_id,
            "profile_id": profile.id,
            "source_bytes": report.source_bytes,
            "output_bytes": report.output_bytes,
            "bytes_saved": report.bytes_saved,
            "reduction_percent": report.reduction_percent,
            "images_seen": report.images_seen,
            "images_optimized": report.images_optimized,
            "images_kept": report.images_kept,
            "cpu_workers": cpu_workers,
        }
        context.report_diagnostic(
            "EPUB optimization completed",
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
            output_format=EPUB,
            proposed_filename=proposed,
            sha256=report.output_sha256,
            size_bytes=report.output_bytes,
            facts=facts,
            warnings=warnings,
        )


PLUGIN = EpubOptimizerTransformer()
