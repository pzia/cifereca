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


def _destination(
    url: str, *, phase: str = "initial",
    on_url_refusal: Callable[[str, str], None] | None = None,
    on_trace_url: Callable[[str, str], None] | None = None,
) -> tuple[str, str, int]:
    """Validate an HTTPS destination; emit only closed refusal categories.

    The optional observer receives two fixed literals (phase and reason),
    never a URL, hostname, path, query, userinfo or HTTP response.
    """

    # Raw URLs (including signed query values) are sent ONLY to an
    # explicitly registered consumer in local trace mode. Keep observation
    # failure isolated from admission and do not format untrusted URLs here.
    if on_trace_url is not None and type(url) is str and 0 < len(url) <= _MAX_URL:
        try:
            if HTTPX_TRANSPORT.trace_enabled() is True:
                on_trace_url(phase, url)
        except Exception:
            pass

    def refuse(reason: str, code: str = "delivery_url_invalid") -> None:
        if on_url_refusal is not None:
            try:
                on_url_refusal(phase, reason)
            except Exception:
                pass
        raise ValueError(code)

    if type(url) is not str or not 0 < len(url) <= _MAX_URL:
        refuse("type_or_length")
    if "#" in url:
        refuse("fragment_marker")
    if any(ord(ch) < 33 or ord(ch) == 127 or ch.isspace() for ch in url):
        refuse("control_or_space")
    if "\\" in url:
        refuse("backslash")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        try:
            port = 443 if parsed.port is None else parsed.port
        except ValueError:
            refuse("port_invalid")
        if parsed.scheme != "https":
            refuse("scheme_not_https")
        if not parsed.netloc or not host:
            refuse("authority_missing")
        if parsed.username is not None or parsed.password is not None:
            refuse("embedded_credentials")
        if "%" in parsed.netloc:
            refuse("encoded_authority")
        if host.endswith("."):
            refuse("trailing_host_dot")
        if not 1 <= port <= 65535:
            refuse("port_invalid")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            refuse("ip_literal", code="delivery_ip_literal_refused")
        if not all(part for part in host.split(".")):
            refuse("empty_host_label")
        # HTTPX and urllib must parse the same authority before any GET.
        from httpx import URL
        normalized = URL(url)
        if normalized.scheme != "https" or normalized.host is None:
            refuse("httpx_authority_invalid")
        if normalized.host.lower().rstrip(".") != host.lower().rstrip("."):
            refuse("parser_disagreement")
        return ("https", host.lower(), port)
    except ValueError as exc:
        if str(exc) in {"delivery_url_invalid", "delivery_ip_literal_refused"}:
            raise
        refuse("parser_invalid")


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
        except httpx.InvalidURL:
            # HTTPX prebuilds response.next_request even when automatic
            # redirects are disabled. Malformed provider Location values
            # (for example javascript:opaque) may fail before our validation.
            # This is a refused redirect, never an authorized second GET.
            raise ValueError("delivery_redirect_invalid") from None
        except httpx.RequestError:
            # HTTPX's exception messages may embed signed URLs and headers.
            raise ValueError("delivery_transport_unavailable") from None
        except OSError:
            # Filesystem errors can include a private staging directory.
            raise ValueError("delivery_staging_unavailable") from None
    return invoked


@_redact_transport_error
def download_attested_https(
    *, producer: str, url: str, target: Path,
    admission_current: Callable[[], bool],
    headers: Mapping[str, str] | None = None, timeout: float = 20,
    max_bytes: int = _MAX_BYTES, max_redirects: int = _MAX_REDIRECTS,
    max_duration: float = 900,
    on_url_refusal: Callable[[str, str], None] | None = None,
    on_trace_url: Callable[[str, str], None] | None = None,
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
    if ((on_url_refusal is not None and not callable(on_url_refusal))
            or (on_trace_url is not None and not callable(on_trace_url))):
        raise ValueError("delivery_parameters_invalid")
    origin = _destination(
        url, on_url_refusal=on_url_refusal, on_trace_url=on_trace_url,
    )
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
    # Single-use signed URLs must never be consumed when the staging parent
    # is already absent/symlinked. Pin its physical identity before any GET:
    # a same-path replacement directory is NOT the selected private stage.
    initial_fd = _directory_fd(physical)
    try:
        initial_stage = os.fstat(initial_fd)
    finally:
        os.close(initial_fd)
    visited = set()
    redirects = 0
    current = url
    with HTTPX_TRANSPORT.public_client(producer=producer, timeout=timeout) as client:
        while True:
            check_current()
            origin = _destination(
                current, phase="redirect" if redirects else "initial",
                on_url_refusal=on_url_refusal,
            )
            if current in visited:
                raise ValueError("delivery_redirect_loop")
            visited.add(current)
            client.cookies.clear()
            per_hop_headers = (
                request_headers if origin == initial else {
                    key: value for key, value in request_headers.items()
                    if (
                        key.lower() in _SAFE_CROSS_ORIGIN
                        # KV's fixed S3 requester-pays marker is public and
                        # non-authenticating. Only its exact constant value
                        # may survive cross-origin redirects, never an
                        # arbitrary supplier header or credential.
                        or (key.lower() == "x-amz-request-payer"
                            and value == "requester")
                    )
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
                    _destination(
                        next_url, phase="redirect",
                        on_url_refusal=on_url_refusal,
                        on_trace_url=on_trace_url,
                    )  # Validate BEFORE next GET.
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
                written_inode = None
                # Keep the writer inode alive through link verification;
                # otherwise an unlink/recreate can reuse st_ino instantly.
                pinned_fd = None
                try:
                    current_stage = os.fstat(descriptor)
                    if ((current_stage.st_dev, current_stage.st_ino)
                            != (initial_stage.st_dev, initial_stage.st_ino)):
                        raise ValueError("delivery_staging_changed")
                    file_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                  | getattr(os, "O_NOFOLLOW", 0))
                    handle = os.open(temporary, file_flags, 0o600, dir_fd=descriptor)
                    temporary_made = True
                    try:
                        digest = hashlib.sha256()
                        total = 0
                        with os.fdopen(handle, "wb") as writer:
                            written_inode = os.fstat(writer.fileno())
                            pinned_fd = os.dup(writer.fileno())
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
                        # The original pinned directory can be renamed while
                        # the stream is in flight. Refuse to hand downstream a
                        # path now pointing into a different directory/owner.
                        recheck_fd = _directory_fd(physical)
                        try:
                            old_dir = os.fstat(descriptor)
                            new_dir = os.fstat(recheck_fd)
                            original = os.stat(
                                temporary, dir_fd=descriptor, follow_symlinks=False,
                            )
                            published = os.stat(
                                physical.name, dir_fd=recheck_fd, follow_symlinks=False,
                            )
                            if ((old_dir.st_dev, old_dir.st_ino)
                                    != (new_dir.st_dev, new_dir.st_ino)
                                    or not stat.S_ISREG(published.st_mode)
                                    or written_inode is None
                                    or (written_inode.st_dev, written_inode.st_ino)
                                    != (published.st_dev, published.st_ino)
                                    or (original.st_dev, original.st_ino)
                                    != (published.st_dev, published.st_ino)):
                                # A name swapped between streaming and link
                                # must never be accepted as the hashed bytes.
                                raise ValueError("delivery_staging_changed")
                            # The same inode can be modified in-place
                            # after we hash downloaded chunks. Inode identity
                            # alone does not prove that the published bytes
                            # still have the receipt digest. Independently
                            # hash the candidate reached by the FINAL path.
                            read_flags = (os.O_RDONLY
                                          | getattr(os, "O_NOFOLLOW", 0))
                            with os.fdopen(
                                os.open(physical.name, read_flags,
                                        dir_fd=recheck_fd), "rb",
                            ) as verifier:
                                opened = os.fstat(verifier.fileno())
                                checked = hashlib.sha256()
                                while True:
                                    block = verifier.read(1024 * 1024)
                                    if not block:
                                        break
                                    checked.update(block)
                                closed = os.fstat(verifier.fileno())
                            for observed in (opened, closed):
                                if (not stat.S_ISREG(observed.st_mode)
                                        or any(
                                            getattr(observed, attr)
                                            != getattr(published, attr)
                                            for attr in (
                                                "st_dev", "st_ino", "st_size",
                                                "st_mtime_ns", "st_ctime_ns",
                                            )
                                        )):
                                    raise ValueError("delivery_staging_changed")
                            if checked.digest() != digest.digest():
                                raise ValueError("delivery_staging_changed")
                            # Hashing a large EPUB/PDF may take time. Refuse
                            # to return a completed candidate if the exact
                            # account/consent changed while verifying bytes.
                            check_current()
                        finally:
                            os.close(recheck_fd)
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
                            except OSError:
                                # The candidate hard link is already durable
                                # when this runs after successful publication.
                                # Cleanup cannot falsely report a failed GET
                                # or provoke a second download.
                                pass
                        if pinned_fd is not None:
                            os.close(pinned_fd)
                    return PublicDelivery(
                        physical, digest.hexdigest(), total, content_type, redirects,
                    )
                finally:
                    os.close(descriptor)


__all__ = ["PublicDelivery", "download_attested_https"]
