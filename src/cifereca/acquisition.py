from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import urlparse

from .models import SearchResult
from .paths import downloads_dir
from .providers.base import ProviderError


AcquisitionKind = Literal["direct_file", "acsm", "url", "already_local"]
DRMKind = Literal["none", "adept", "unknown"]


@dataclass(frozen=True)
class Acquisition:
    """Normalized acquisition produced by any provider workflow.

    This is deliberately queue-ready without implementing the persistent queue
    yet.  It carries both provenance (provider/credential/borrower) and the two
    local file roles needed by the next step: an optional ACSM and the final
    readable artifact (EPUB/PDF/audio/etc.).

    ``action``, ``target_dir`` and ``command`` are execution metadata kept for
    0.3.2 API/UI compatibility. ``path`` and ``url`` are compatibility aliases.
    """

    provider_id: str
    credential_id: str
    account_id: str | None
    item_id: str
    loan_id: str | None
    kind: AcquisitionKind
    source_url: str | None
    local_path: Path | None
    mime_type: str | None
    drm: DRMKind
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    acsm_path: Path | None = None
    artifact_path: Path | None = None
    target_dir: Path | None = None
    action: str = "download"
    command: tuple[str, ...] = ()

    @property
    def path(self) -> Path | None:
        """0.3.2 compatibility alias for the concrete local output."""
        return self.local_path or self.artifact_path or self.acsm_path

    @property
    def url(self) -> str:
        """0.3.2 compatibility alias."""
        return self.source_url or ""


@dataclass(frozen=True)
class AcquisitionResult:
    """Legacy 0.3.2 result shape retained for import/constructor compatibility."""

    action: str
    url: str
    target_dir: Path
    path: Path | None = None
    command: tuple[str, ...] = ()


class AcquisitionManager:
    """Resolve download policy independently from concrete provider plugins.

    The core default stays deliberately boring: the provider performs its
    normal download into the resolved target directory. A global or provider
    command can override that action. Post-download hooks are then run after a
    concrete file exists.

    Provider-level settings live under ``providers.settings.<provider>.downloads``
    so they are shared by all credential identities of one plugin.
    """

    def __init__(self, config: Any, diagnostics: Any) -> None:
        self.config = config
        self.diagnostics = diagnostics

    @staticmethod
    def content_type_for(item: SearchResult) -> str:
        value = str(item.content_type or "").strip().lower()
        if value:
            return value
        if item.medium == "physical":
            return "physical"
        probe = " ".join([str(item.type or ""), str(item.format or "")]).casefold()
        if any(token in probe for token in ("audio", "sonore", "mp3", "m4a", "ogg", "opus", "flac")):
            return "audiobook"
        return "ebook"

    @staticmethod
    def _clean_command(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(x) for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return shlex.split(value)
        return []

    def _provider_downloads(self, provider_id: str) -> dict[str, Any]:
        settings = self.config.get(f"providers.settings.{provider_id}.downloads", {})
        return dict(settings) if isinstance(settings, dict) else {}

    @staticmethod
    def _path_from_setting(value: Any, fallback_root: Path) -> Path | None:
        text = os.path.expandvars(os.path.expanduser(str(value or "").strip()))
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute():
            path = fallback_root / path
        return path

    def target_dir(self, provider_id: str, content_type: str) -> Path:
        fallback = downloads_dir()
        global_cfg = self.config.get("downloads", {})
        global_cfg = dict(global_cfg) if isinstance(global_cfg, dict) else {}
        provider_cfg = self._provider_downloads(provider_id)

        provider_types = provider_cfg.get("content_type_directories", {})
        provider_types = dict(provider_types) if isinstance(provider_types, dict) else {}
        global_types = global_cfg.get("content_type_directories", {})
        global_types = dict(global_types) if isinstance(global_types, dict) else {}

        # Priority explicitly documented in 0.3.2:
        # provider+content > provider > global content > global > XDG fallback.
        candidates = [
            provider_types.get(content_type),
            provider_cfg.get("directory"),
            global_types.get(content_type),
            global_cfg.get("directory"),
        ]
        for raw in candidates:
            resolved = self._path_from_setting(raw, fallback)
            if resolved is not None:
                resolved.mkdir(parents=True, exist_ok=True)
                return resolved
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def override_command(self, provider_id: str) -> list[str]:
        provider = self._provider_downloads(provider_id)
        command = self._clean_command(provider.get("command"))
        if command:
            return command
        global_cfg = self.config.get("downloads", {})
        if isinstance(global_cfg, dict):
            return self._clean_command(global_cfg.get("command"))
        return []

    def post_download_commands(self, provider_id: str) -> list[list[str]]:
        commands: list[list[str]] = []
        global_cfg = self.config.get("downloads", {})
        if isinstance(global_cfg, dict):
            cmd = self._clean_command(global_cfg.get("post_download_command"))
            if cmd:
                commands.append(cmd)
        provider = self._provider_downloads(provider_id)
        cmd = self._clean_command(provider.get("post_download_command"))
        if cmd and cmd not in commands:
            commands.append(cmd)
        return commands

    @staticmethod
    def acquisition_url(item: SearchResult) -> str:
        return str(item.download_url or item.friendly_url or "").strip()

    @staticmethod
    def _guess_extension(item: SearchResult, url: str) -> str:
        suffix = Path(urlparse(url).path).suffix
        # Gallica uses the virtual suffix .audio for streams that are in fact
        # MP3; prefer normalized format metadata in that case.
        if suffix and suffix.lower() != ".audio" and 1 < len(suffix) <= 10:
            return suffix
        fmt = str(item.format or "").strip().lower()
        mapping = {
            "epub": ".epub", "pdf": ".pdf", "mp3": ".mp3", "m4a": ".m4a",
            "ogg": ".ogg", "opus": ".opus", "flac": ".flac", "wav": ".wav",
            "acsm": ".acsm",
        }
        return mapping.get(fmt, "")

    @staticmethod
    def mime_type_for(path: Path | None, item: SearchResult | None = None) -> str | None:
        suffix = (path.suffix.lower() if path else "")
        by_suffix = {
            ".epub": "application/epub+zip",
            ".pdf": "application/pdf",
            ".acsm": "application/vnd.adobe.adept+xml",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
            ".opus": "audio/opus",
            ".flac": "audio/flac",
            ".wav": "audio/wav",
            ".m3u8": "application/vnd.apple.mpegurl",
        }
        if suffix in by_suffix:
            return by_suffix[suffix]
        fmt = str(item.format or "").strip().lower() if item else ""
        return by_suffix.get(f".{fmt}") if fmt else None

    @staticmethod
    def _safe_filename(value: str) -> str:
        cleaned = "".join(ch if (ch.isalnum() or ch in " ._()-") else "_" for ch in value).strip(" ._")
        return cleaned[:160] or "download"

    def suggested_path(self, item: SearchResult, target_dir: Path, url: str) -> Path:
        ext = self._guess_extension(item, url)
        return target_dir / f"{self._safe_filename(item.title or 'download')}{ext}"

    def placeholders(
        self,
        *,
        item: SearchResult,
        target_dir: Path,
        url: str,
        file_path: Path | None,
    ) -> dict[str, str]:
        return {
            "url": url,
            "file": str(file_path or ""),
            "directory": str(target_dir),
            "title": item.title or "",
            "record_id": item.record_id or "",
            "provider": item.source_provider or "",
            "content_type": self.content_type_for(item),
            "format": item.format or "",
        }

    @staticmethod
    def _expand(command: Iterable[str], values: dict[str, str]) -> list[str]:
        result: list[str] = []
        for token in command:
            try:
                result.append(str(token).format_map(values))
            except KeyError as exc:
                raise ProviderError(f"Placeholder de commande inconnu : {exc.args[0]}") from exc
        return result

    def acquisition_from_direct(
        self,
        item: SearchResult,
        *,
        path: Path | None,
        url: str,
        target_dir: Path,
        action: str = "download",
        command: Iterable[str] = (),
    ) -> Acquisition:
        return Acquisition(
            provider_id=item.source_provider,
            credential_id=item.source_credential,
            account_id=None,
            item_id=str(item.record_id or item.id),
            loan_id=None,
            kind="direct_file" if path else "url",
            source_url=url or None,
            local_path=path,
            mime_type=self.mime_type_for(path, item),
            drm="none",
            metadata={
                "title": item.title,
                "author": item.author,
                "cover_url": item.thumbnail_url,
                "content_type": self.content_type_for(item),
                "format": item.format,
            },
            artifact_path=path,
            target_dir=target_dir,
            action=action,
            command=tuple(command),
        )

    def run_override(self, item: SearchResult, target_dir: Path) -> Acquisition:
        url = self.acquisition_url(item)
        if not url:
            raise ProviderError(f"{item.source_provider}: aucun lien d'acquisition utilisable")
        command = self.override_command(item.source_provider)
        if not command:
            raise ProviderError("Aucune commande de téléchargement configurée")
        expected = self.suggested_path(item, target_dir, url)
        values = self.placeholders(item=item, target_dir=target_dir, url=url, file_path=expected)
        argv = self._expand(command, values)
        subprocess.run(argv, check=True)
        path = expected if expected.is_file() else None
        self.diagnostics.add(
            "DIGITAL", "download override command", "ok",
            detail={"provider": item.source_provider, "content_type": self.content_type_for(item), "file_detected": bool(path)},
        )
        if path:
            self.run_post_download(item, path, url, target_dir)
        return self.acquisition_from_direct(
            item, path=path, url=url, target_dir=target_dir, action="command", command=argv,
        )

    def run_post_download(self, item: SearchResult, path: Path, url: str, target_dir: Path) -> None:
        if not path.is_file():
            return
        values = self.placeholders(item=item, target_dir=target_dir, url=url, file_path=path)
        for command in self.post_download_commands(item.source_provider):
            argv = self._expand(command, values)
            subprocess.run(argv, check=True)
            self.diagnostics.add(
                "DIGITAL", "post download hook", "ok",
                detail={"provider": item.source_provider, "content_type": self.content_type_for(item)},
            )
