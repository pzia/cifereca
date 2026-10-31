from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

from cifereca.plugin_api import Conflict, TemporaryUnavailable, UnsupportedOperation, inspect_artifact, publish_artifact_file


_REDIRECTS = {301, 302, 303, 307, 308}
_SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization"}
_FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    port = parsed.port if parsed.port is not None else (
        443 if scheme == "https" else 80 if scheme == "http" else None
    )
    return scheme, (parsed.hostname or "").casefold(), port


def _validate_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise UnsupportedOperation("transport.http: URL HTTP/HTTPS requise")
    if parsed.username is not None or parsed.password is not None:
        raise UnsupportedOperation("transport.http: identifiants interdits dans l'URL")
    return url


def _validate_core_protection(source: Mapping[str, Any]) -> None:
    """Validate the normalized Core proof without constraining direct provider calls."""
    core_policy = any(key in source for key in ("handling", "drm", "protection"))
    if not core_policy:
        return

    proof = source.get("protection")
    if not isinstance(proof, Mapping):
        raise UnsupportedOperation("transport.http: preuve de protection Core requise")
    handling = str(proof.get("handling") or "").strip().casefold()
    if handling != "http_plain":
        raise UnsupportedOperation("transport.http: handling Core non http_plain")
    if proof.get("protected") is not False or proof.get("attested") is not True:
        raise UnsupportedOperation("transport.http: preuve Core non attestée")
    source_handling = str(source.get("handling") or "").strip().casefold()
    if source_handling and source_handling != handling:
        raise UnsupportedOperation("transport.http: preuve Core incohérente")

    source_drm = source.get("drm")
    if isinstance(source_drm, Mapping):
        if "protected" in source_drm and source_drm.get("protected") is not False:
            raise UnsupportedOperation("transport.http: preuve DRM Core incohérente")
    elif source_drm is not None:
        normalized = "".join(
            char for char in str(source_drm).casefold()
            if char.isalnum()
        )
        if normalized and normalized not in {"none", "nodrm", "signednodrm"}:
            raise UnsupportedOperation("transport.http: preuve DRM Core incohérente")


def _safe_headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise UnsupportedOperation("transport.http: headers invalides")
    out: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name or "").strip()
        val = str(raw_value or "").strip()
        lower = name.casefold()
        if not name or lower in _FORBIDDEN_HEADERS:
            continue
        if "\r" in name or "\n" in name or "\r" in val or "\n" in val:
            raise UnsupportedOperation("transport.http: header invalide")
        out[name] = val
    return out


class HttpTransport:
    def __init__(self, context) -> None:
        self.context = context
        self.config = dict(getattr(context, "config", {}) or {})
        self.timeout_seconds = float(self.config.get("timeout_seconds", 30) or 30)
        self.max_download_bytes = int(self.config.get("max_download_bytes", 256 * 1024 * 1024) or 256 * 1024 * 1024)
        self.max_redirects = int(self.config.get("max_redirects", 8) or 8)
        self.client = HTTPX_TRANSPORT.client(
            producer="transport.http",
            follow_redirects=False,
            trust_env=False,
        )
        self.logger = getattr(context, "logger", logging.getLogger("cifereca.transport.http"))

    def matches(self, source: Mapping[str, Any]) -> bool:
        try:
            _validate_url(source.get("url") if isinstance(source, Mapping) else None)
            return True
        except Exception:
            return False

    def _limits(self, source: Mapping[str, Any]) -> tuple[float, int, int]:
        requested_timeout = float(source.get("timeout_seconds") or self.timeout_seconds)
        timeout = max(1.0, min(self.timeout_seconds, requested_timeout))
        requested_bytes = int(source.get("max_bytes") or self.max_download_bytes)
        maximum = max(1, min(self.max_download_bytes, requested_bytes))
        requested_redirects = int(source.get("max_redirects") if source.get("max_redirects") is not None else self.max_redirects)
        redirects = max(0, min(self.max_redirects, requested_redirects))
        return timeout, maximum, redirects

    def fetch(self, source: Mapping[str, Any], destination: Path) -> Path:
        if not isinstance(source, Mapping):
            raise UnsupportedOperation("transport.http: source invalide")
        _validate_core_protection(source)
        url = _validate_url(source.get("url"))
        headers = _safe_headers(source.get("headers"))
        timeout, maximum, max_redirects = self._limits(source)
        allowed = {
            str(value or "").split(";", 1)[0].strip().casefold()
            for value in (source.get("allowed_content_types") or ())
            if str(value or "").strip()
        }
        allow_downgrade = bool(source.get("allow_downgrade", False))
        # Opt-in. Legacy callers retain their ordinary redirect policy.
        redirect_policy = source.get("redirect_policy", "any")
        if type(redirect_policy) is not str or redirect_policy not in {
            "any", "same_origin"
        }:
            raise UnsupportedOperation("transport.http: politique de redirection invalide")
        report_http_status = source.get("classify_http_status", False)
        if type(report_http_status) is not bool:
            raise UnsupportedOperation("transport.http: classification HTTP invalide")
        overwrite = bool(source.get("overwrite", False))
        destination = Path(destination)
        if not destination.name:
            raise UnsupportedOperation("transport.http: destination invalide")
        destination.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".part",
            dir=destination.parent,
        )
        temporary = Path(tmp_name)
        os.close(fd)
        final_url = url
        bytes_written = 0
        status = None

        try:
            current = url
            current_headers = dict(headers)
            for redirect_index in range(max_redirects + 1):
                try:
                    with self.client.stream(
                        "GET",
                        current,
                        headers=current_headers,
                        timeout=timeout,
                    ) as response:
                        status = response.status_code
                        if status in _REDIRECTS:
                            location = str(response.headers.get("location") or "").strip()
                            if not location:
                                raise TemporaryUnavailable("transport.http: redirection sans destination")
                            if redirect_index >= max_redirects:
                                raise TemporaryUnavailable("transport.http: trop de redirections")
                            next_url = _validate_url(urljoin(str(response.url), location))
                            if redirect_policy == "same_origin":
                                try:
                                    same_origin = _origin(next_url) == _origin(url)
                                except ValueError:
                                    raise UnsupportedOperation(
                                        "transport.http: destination de redirection invalide"
                                    ) from None
                                # OPDS redirects must remain on the original
                                # public origin. No other host is contacted.
                                if not same_origin:
                                    raise UnsupportedOperation(
                                        "transport.http: redirection hors origine refusée"
                                    )
                            if urlsplit(str(response.url)).scheme.casefold() == "https" and urlsplit(next_url).scheme.casefold() == "http" and not allow_downgrade:
                                raise UnsupportedOperation("transport.http: redirection HTTPS vers HTTP refusée")
                            if _origin(str(response.url)) != _origin(next_url):
                                current_headers = {
                                    key: value for key, value in current_headers.items()
                                    if key.casefold() not in _SENSITIVE_HEADERS
                                }
                            current = next_url
                            continue

                        response.raise_for_status()
                        final_url = str(response.url)
                        media_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
                        if media_type in _HTML_TYPES:
                            raise UnsupportedOperation("transport.http: réponse HTML refusée")
                        if allowed and media_type and media_type not in allowed:
                            raise UnsupportedOperation(f"transport.http: content-type refusé ({media_type})")
                        declared = str(response.headers.get("content-length") or "").strip()
                        if declared.isdigit() and int(declared) > maximum:
                            raise UnsupportedOperation("transport.http: fichier trop volumineux")

                        with temporary.open("wb") as output:
                            for chunk in response.iter_bytes():
                                if not chunk:
                                    continue
                                bytes_written += len(chunk)
                                if bytes_written > maximum:
                                    raise UnsupportedOperation("transport.http: fichier trop volumineux")
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                        break
                except httpx.HTTPStatusError as exc:
                    if report_http_status:
                        status = exc.response.status_code
                        code = ("auth_required" if status == 401 else
                                "access_denied" if status == 403 else
                                "not_found" if status in {404, 410} else
                                "rate_limited" if status == 429 else
                                "service_unavailable")
                        raise TemporaryUnavailable(
                            f"transport.http: {code}"
                        ) from None
                    raise TemporaryUnavailable("transport.http: erreur réseau (HTTPStatusError)") from exc
                except httpx.HTTPError as exc:
                    raise TemporaryUnavailable(f"transport.http: erreur réseau ({type(exc).__name__})") from exc
            else:
                raise TemporaryUnavailable("transport.http: redirections non résolues")

            if bytes_written <= 0:
                raise UnsupportedOperation("transport.http: réponse vide")

            expected_format = str(source.get("expected_format") or "").strip().casefold()
            if expected_format:
                inspected = inspect_artifact(temporary, expected_format, strict_structure=True)
                if not inspected.valid:
                    raise UnsupportedOperation(
                        f"transport.http: artefact invalide ({inspected.reason or 'format inconnu'})"
                    )
                if inspected.protected is True and not bool(source.get("allow_protected", False)):
                    raise UnsupportedOperation("transport.http: artefact protégé refusé")

            try:
                publish_artifact_file(
                    temporary,
                    destination,
                    overwrite=overwrite,
                    accept_identical=True,
                )
            except FileExistsError:
                raise Conflict(
                    f"transport.http: destination déjà existante: {destination.name}"
                ) from None

            self.logger.info(
                "TRACE http-transport fetch host=%s status=%s bytes=%s redirects=%s file=%s",
                urlsplit(final_url).hostname or "",
                status,
                bytes_written,
                redirect_index,
                destination.name,
            )
            return destination
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        self.client.close()


class HttpTransportPlugin:
    def __init__(self) -> None:
        self.service: HttpTransport | None = None

    def activate(self, context):
        self.service = HttpTransport(context)
        return self.service

    def deactivate(self) -> None:
        if self.service is not None:
            self.service.close()
        self.service = None


PLUGIN = HttpTransportPlugin()
