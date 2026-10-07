"""Cifereca public-plugin entry point for EPUB/PDF e-ink optimization."""
from __future__ import annotations

import weakref

from cifereca.plugin_api import UiActionContribution, UiPageContribution


class EpubOptimizerPlugin:
    def __init__(self) -> None:
        self.context = None
        self._controllers: weakref.WeakSet = weakref.WeakSet()
        self._pending_artifact = None
        self._pending_action_host = None

    def activate(self, context):
        self.context = context
        context.logger.info("EPUB/PDF optimizer plugin activated")
        return self

    @staticmethod
    def _visible_for_document(action_context) -> bool:
        artifact = getattr(action_context, "artifact", None)
        fmt = getattr(getattr(artifact, "format", None), "format", "") if artifact is not None else ""
        drm = getattr(getattr(artifact, "format", None), "drm", "none") if artifact is not None else "none"
        return (
            artifact is not None
            and str(fmt).casefold() in {"epub", "pdf"}
            and str(drm or "none").casefold() in {"", "none"}
        )

    _visible_for_epub = _visible_for_document

    def _open_from_detail(self, host):
        ctx = host.action_context()
        artifact = getattr(ctx, "artifact", None)
        if artifact is None:
            host.toast("Aucun EPUB/PDF local disponible pour cette action")
            return None
        self._pending_artifact = artifact
        self._pending_action_host = host
        # Core041 exposes artifact.execution; the page keeps profile selection
        # but delegates all leases, persistence and placement changes to Core.
        host.navigate("epub-optimize")
        return artifact

    def pages(self):
        action = UiActionContribution(
            id="epub-optimize-current",
            title="Optimiser…",
            callback=self._open_from_detail,
            icon_name="document-save-symbolic",
            target="book.detail",
            visible_when=self._visible_for_document,
            enabled_when=self._visible_for_document,
            cancellable=True,
            progress=True,
            result_kind="none",
        )
        return [
            UiPageContribution(
                route="epub-optimize",
                title="Optimiser EPUB / PDF",
                icon_name="document-save-symbolic",
                order=75,
                factory=self._build_page,
                group="usage",
                actions=(action,),
            )
        ]

    def _build_page(self, host):
        if self.context is None:
            raise RuntimeError("plugin not activated")
        from cifereca_epub_optimizer.ui import OptimizerPage

        controller = OptimizerPage(
            host=host,
            context=self.context,
            artifact=self._pending_artifact,
            action_host=self._pending_action_host,
        )
        self._pending_artifact = None
        self._pending_action_host = None
        self._controllers.add(controller)
        return controller.widget

    def deactivate(self):
        for controller in list(self._controllers):
            try:
                controller.cancel()
            except Exception:
                pass
        if self.context is not None:
            self.context.logger.info("EPUB/PDF optimizer plugin deactivated")
        self._controllers.clear()
        self._pending_artifact = None
        self._pending_action_host = None
        self.context = None


PLUGIN = EpubOptimizerPlugin()
