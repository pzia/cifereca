"""#3867 — provider-neutral HTTPS delivery from an already attested offer.

The caller must establish exact source, account and consent before invoking
this transport. This module does not grant a right or publish a Placement.
Only the Core public-peer-pinned HTTPX client may perform the requests.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import hashlib
import ipaddress
import os
from pathlib import Path
import secrets
import stat
from time import monotonic as _monotonic
from typing import Callable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from ..http_trace import HTTPX_TRANSPORT

_MAX_REDIRECTS = 8
_MAX_BYTES = 512 * 1024 * 1024
_MAX_URL = 16384
_STRIP_CROSS_ORIGIN = frozenset({"authorization", "cookie", "referer", "origin",
                                  "proxy-authorization", "x-csrf-token",
                                  "x-xsrf-token"})
_SAFE_CROSS_ORIGIN = frozenset({"accept", "accept-language", "user-agent"})


@dataclass(frozen=True)
class PublicDelivery:
    path: Path
    sha256: str
    size: int
    content_type: str
    redirects: int


def _destination(url: str) -> tuple[str, str, int]:
    """Validate the raw HTTPS authority before HTTPX parses or rewrites it."""
    if (type(url) is not str or not 0 < len(url) <= _MAX_URL
            or any(ord(ch) < 33 or ord(ch) == 127 or ch == "\\" or ch.isspace() for ch in url)
            or "#" in url):
        raise ValueError("delivery_url_invalid")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = 443 if parsed.port is None else parsed.port
        if (parsed.scheme != "https" or not parsed.netloc or not host
                or parsed.username is not None or parsed.password is not None
                or "%" in parsed.netloc or host.endswith(".")
                or not 1 <= port <= 65535):
            raise ValueError("delivery_url_invalid")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("delivery_ip_literal_refused")
        if not all(part for part in host.split(".")):
            raise ValueError("delivery_url_invalid")
        # Enforce URL parser agreement, including punycode and bracket syntax.
        from httpx import URL
        normalized = URL(url)
        if normalized.scheme != "https" or normalized.host is None:
            raise ValueError("delivery_url_invalid")
        if normalized.host.lower().rstrip(".") != host.lower().rstrip("."):
            raise ValueError("delivery_url_invalid")
        return ("https", host.lower(), port)
    except ValueError as exc:
        if str(exc) == "delivery_ip_literal_refused":
            raise
        raise ValueError("delivery_url_invalid") from None


def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {"Accept": "application/octet-stream, application/epub+zip, */*"}
    if not isinstance(headers, Mapping) or len(headers) > 32:
        raise ValueError("delivery_headers_invalid")
    result = {}
    for key, value in headers.items():
        if (type(key) is not str or type(value) is not str
                or not key or len(key) > 128 or len(value) > 8192
                or any(not (ch.isascii() and (ch.isalnum() or ch == "-"))
                       for ch in key)
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
                or key.lower() in {"host", "connection", "content-length",
                                    "transfer-encoding", "proxy-authorization"}):
            raise ValueError("delivery_headers_invalid")
        result[key] = value
    return result


def _directory_fd(target: Path) -> int:
    """Open every parent component with O_NOFOLLOW, pinned by directory fd.

    A check-then-open Path.is_symlink() walk has an ancestor replacement race:
    openat from '/' closes it before any output file is created.
    """
    physical = Path(os.path.abspath(target.expanduser()))
    if physical.name in ("", ".", ".."):
        raise ValueError("delivery_staging_unsafe")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open("/", flags)
        for name in physical.parent.parts[1:]:
            next_descriptor = os.open(name, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("delivery_staging_unsafe")
        return descriptor
    except (OSError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        raise ValueError("delivery_staging_unsafe") from None


def _admitted(callback: Callable[[], bool]) -> None:
    # Recheck the Core admission before first GET, each hop, each chunk
    # and immediately before publishing the target name.
    try:
        if callback() is True:
            return
    except Exception:
        pass
    raise ValueError("delivery_admission_changed")


def _redact_transport_error(fn):
    @wraps(fn)
    def invoked(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.RequestError:
            # HTTPX's exception messages may embed signed URLs and headers.
            # Callers can safely include the closed code in normal logs.
            raise ValueError("delivery_transport_unavailable") from None
    return invoked


@_redact_transport_error
def download_attested_https(
    *, producer: str, url: str, target: Path,
    admission_current: Callable[[], bool],
    headers: Mapping[str, str] | None = None, timeout: float = 20,
    max_bytes: int = _MAX_BYTES, max_redirects: int = _MAX_REDIRECTS,
    max_duration: float = 900,
) -> PublicDelivery:
    """Stream an attested GET into a new staging file, with no URL in results.

    No domain allowlist. Every HTTPS hop is revalidated, every TCP peer is
    pinned publicly by HTTPX_TRANSPORT.public_client, and no cookie jar or
    bearer header is transferred to another origin. A failed download never
    appears under the target name. Consumers still must validate EPUB/ACSM
    bytes, bind scope/offer and complete Core's canonical publication.
    """
    if (type(producer) is not str or not 0 < len(producer) <= 128
            or not isinstance(target, Path) or not callable(admission_current)
            or type(max_bytes) is not int or not 0 < max_bytes <= _MAX_BYTES
            or type(max_redirects) is not int
            or not 0 <= max_redirects <= _MAX_REDIRECTS
            or type(max_duration) not in (int, float)
            or not 0 < max_duration <= 3600):
        raise ValueError("delivery_parameters_invalid")
    origin = _destination(url)
    deadline = _monotonic() + max_duration

    def check_current():
        if _monotonic() > deadline:
            raise ValueError("delivery_deadline")
        _admitted(admission_current)

    check_current()
    initial = origin
    request_headers = _headers(headers)
    physical = Path(os.path.abspath(target.expanduser()))
    if physical.exists() or physical.is_symlink():
        raise ValueError("delivery_target_exists")
    visited = set()
    redirects = 0
    current = url
    with HTTPX_TRANSPORT.public_client(producer=producer, timeout=timeout) as client:
        while True:
            check_current()
            origin = _destination(current)
            if current in visited:
                raise ValueError("delivery_redirect_loop")
            visited.add(current)
            client.cookies.clear()
            per_hop_headers = (
                request_headers if origin == initial else {
                    key: value for key, value in request_headers.items()
                    if key.lower() in _SAFE_CROSS_ORIGIN
                }
            )
            # The transport refuses private/non-global DNS answers at the
            # actual TCP connection, not only at a preliminary DNS lookup.
            with client.stream("GET", current, headers=per_hop_headers,
                               follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirects >= max_redirects:
                        raise ValueError("delivery_redirect_limit")
                    location = response.headers.get("location")
                    if (not location or any(ord(ch) < 33 or ord(ch) == 127
                                            or ch == "\\" or ch.isspace() for ch in location)):
                        raise ValueError("delivery_redirect_missing")
                    next_url = urljoin(current, location)
                    _destination(next_url)  # Validate BEFORE next GET.
                    current = next_url
                    redirects += 1
                    continue
                if response.status_code != 200:
                    raise ValueError("delivery_http_status")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if content_type in ("text/html", "application/xhtml+xml"):
                    raise ValueError("delivery_html_instead_of_file")
                raw_length = response.headers.get("content-length")
                if raw_length is not None:
                    try:
                        length = int(raw_length)
                    except ValueError:
                        raise ValueError("delivery_length_invalid") from None
                    if not 0 <= length <= max_bytes:
                        raise ValueError("delivery_size_exceeded")

                descriptor = _directory_fd(physical)
                temporary = ".delivery-" + secrets.token_hex(16) + ".part"
                temporary_made = False
                linked = False
                try:
                    file_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                  | getattr(os, "O_NOFOLLOW", 0))
                    handle = os.open(temporary, file_flags, 0o600, dir_fd=descriptor)
                    temporary_made = True
                    try:
                        digest = hashlib.sha256()
                        total = 0
                        with os.fdopen(handle, "wb") as writer:
                            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                                check_current()
                                total += len(chunk)
                                if total > max_bytes:
                                    raise ValueError("delivery_size_exceeded")
                                digest.update(chunk)
                                writer.write(chunk)
                            writer.flush()
                            os.fsync(writer.fileno())
                        if not total:
                            raise ValueError("delivery_empty")
                        check_current()
                        # Atomic no-replace publication within one pinned
                        # directory: a concurrent target can never be clobbered.
                        os.link(temporary, physical.name, src_dir_fd=descriptor,
                                dst_dir_fd=descriptor, follow_symlinks=False)
                        linked = True
                        os.fsync(descriptor)
                    except BaseException:
                        # A failed fsync or concurrent cancellation must not
                        # leave the failed attempt advertised as a completed
                        # candidate. Unlink only our own inode, never a
                        # substituted target.
                        if linked:
                            try:
                                own = os.stat(temporary, dir_fd=descriptor,
                                              follow_symlinks=False)
                                final = os.stat(physical.name, dir_fd=descriptor,
                                                follow_symlinks=False)
                                if ((own.st_dev, own.st_ino)
                                        == (final.st_dev, final.st_ino)):
                                    os.unlink(physical.name, dir_fd=descriptor)
                            except OSError:
                                pass
                        raise
                    finally:
                        if temporary_made:
                            try:
                                os.unlink(temporary, dir_fd=descriptor)
                                os.fsync(descriptor)
                            except FileNotFoundError:
                                pass
                    return PublicDelivery(
                        physical, digest.hexdigest(), total, content_type, redirects,
                    )
                finally:
                    os.close(descriptor)


__all__ = ["PublicDelivery", "download_attested_https"]
