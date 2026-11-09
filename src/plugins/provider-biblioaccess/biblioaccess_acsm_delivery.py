"""Stage one owned BiblioAccess loan ACSM from its fresh MyLoans signed action.

The signed URL is ephemeral and never returned to Core, included in errors,
or written in library receipts. DRM fulfillment belongs to Core Kobo-Adept.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import os
from urllib.parse import urlsplit

import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from biblioaccess_observations import MAX_ACSM_BYTES, inspect_acsm_structure


class ACSMDeliveryError(ValueError):
    """Closed error codes; no private token, URL or response content."""


def _delivery_origin(url: str) -> str:
    """Strict known-provider URL validation; never accept arbitrary hosts."""
    if type(url) is not str or len(url) > 8192:
        raise ACSMDeliveryError("biblioaccess_acsm_url_invalid")
    try:
        parts = urlsplit(url)
        numilog = (parts.hostname == "entrepot2r.numilog.com"
                   and parts.path == "/fulfillment/URLLink.acsm"
                   and bool(parts.query))
        pnb = (parts.hostname == "pnb-dilicom.centprod.com"
               and parts.path.startswith("/")
               and parts.path.endswith("/do")
               and not parts.path.startswith("//")
               and all(segment not in ("", ".", "..")
                       for segment in parts.path[1:].split("/")))
        if (parts.scheme != "https" or not (numilog or pnb)
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.fragment):
            raise ACSMDeliveryError("biblioaccess_acsm_url_invalid")
        return parts.hostname
    except ValueError:
        raise ACSMDeliveryError("biblioaccess_acsm_url_invalid") from None


def stage_loan_acsm(url: str, target_dir: Path, *, transport=None) -> Path:
    """Direct HTTPS GET of the observed signed offer; no replay restrictions.

    Every call fetches the current link. Existing files do not suppress a
    requested re-download, and the caller's source session owns the identity.
    """
    origin = _delivery_origin(url)
    if not isinstance(target_dir, Path):
        raise ACSMDeliveryError("biblioaccess_acsm_destination_invalid")

    client = HTTPX_TRANSPORT.client(
        producer="provider.biblioaccess.acsm",
        timeout=httpx.Timeout(25, connect=8),
        follow_redirects=False, trust_env=False, transport=transport,
    )
    try:
        try:
            current = url
            for hop in range(3):
                # Stream rather than buffering untrusted provider responses.
                with client.stream(
                    "GET", current, follow_redirects=False,
                    headers={"Accept": "application/vnd.adobe.adept+xml, application/xml, */*"},
                ) as response:
                    status = response.status_code
                    if status in (301, 302, 303, 307, 308):
                        if origin != "pnb-dilicom.centprod.com" or hop >= 2:
                            raise ACSMDeliveryError("biblioaccess_acsm_redirect")
                        location = response.headers.get("location", "")
                        from urllib.parse import urljoin
                        target = urljoin(current, location)
                        try:
                            next_parts = urlsplit(target)
                            if (not location or location.startswith("//")
                                    or next_parts.scheme != "https"
                                    or next_parts.hostname != origin
                                    or next_parts.port not in (None, 443)
                                    or next_parts.username is not None
                                    or next_parts.password is not None
                                    or next_parts.fragment
                                    or not next_parts.path.startswith("/")):
                                raise ACSMDeliveryError("biblioaccess_acsm_redirect")
                        except ValueError:
                            raise ACSMDeliveryError("biblioaccess_acsm_redirect") from None
                        current = target
                        continue
                    if status != 200:
                        code = (
                            "biblioaccess_acsm_redirect" if 300 <= status < 400 else
                            "biblioaccess_acsm_denied" if status in (401, 403) else
                            "biblioaccess_acsm_missing" if status == 404 else
                            "biblioaccess_acsm_rate_limited" if status == 429 else
                            "biblioaccess_acsm_remote_error" if status >= 500 else
                            "biblioaccess_acsm_unavailable"
                        )
                        raise ACSMDeliveryError(code)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_ACSM_BYTES:
                            raise ACSMDeliveryError("biblioaccess_acsm_body_limit")
                        chunks.append(chunk)
                    break
        except httpx.HTTPError:
            raise ACSMDeliveryError("biblioaccess_acsm_transport_unavailable") from None
        data = b"".join(chunks)
        try:
            structure = inspect_acsm_structure(data)
        except ValueError:
            raise ACSMDeliveryError("biblioaccess_acsm_structure_invalid") from None
        if structure.drm != "adobe-adept" or structure.format != "acsm":
            raise ACSMDeliveryError("biblioaccess_acsm_structure_invalid")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target_dir, prefix="biblioaccess-",
                suffix=".part", delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(data)
            destination = tmp_path.with_suffix(".acsm")
            try:
                os.replace(tmp_path, destination)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
            return destination
        except OSError:
            raise ACSMDeliveryError("biblioaccess_acsm_stage_unavailable") from None
    finally:
        client.close()
