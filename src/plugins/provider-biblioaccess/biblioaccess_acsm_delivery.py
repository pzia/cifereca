"""Stage one owned BiblioAccess loan ACSM from its fresh MyLoans signed action.

The signed URL is ephemeral and never returned to Core, included in errors,
or written in library receipts. DRM fulfillment belongs to Core Kobo-Adept.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import os
from urllib.parse import urljoin

import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from biblioaccess_observations import MAX_ACSM_BYTES, inspect_acsm_structure
from biblioaccess_delivery_url import (
    classify_loan_delivery, allowed_delivery_redirect,
)


class ACSMDeliveryError(ValueError):
    """Closed error codes; no private token, URL or response content."""


def stage_loan_acsm(url: str, target_dir: Path, *, transport=None) -> Path:
    """Direct HTTPS GET of the observed signed offer; no replay restrictions.

    Every call fetches the current link. Existing files do not suppress a
    requested re-download, and the caller's source session owns the identity.
    """
    if classify_loan_delivery(url) is None:
        raise ACSMDeliveryError("biblioaccess_acsm_url_invalid")
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
            visited: set[str] = set()
            for _hop in range(4):
                if current in visited:
                    raise ACSMDeliveryError("biblioaccess_acsm_redirect_cycle")
                visited.add(current)
                with client.stream(
                    "GET", current, follow_redirects=False,
                    headers={
                        "Accept": "application/vnd.adobe.adept+xml, application/xml, */*",
                    },
                ) as response:
                    status = response.status_code
                    if 300 <= status < 400:
                        location = response.headers.get("location")
                        if not location:
                            raise ACSMDeliveryError("biblioaccess_acsm_redirect")
                        target = urljoin(current, location)
                        if not allowed_delivery_redirect(target):
                            raise ACSMDeliveryError("biblioaccess_acsm_redirect_refused")
                        current = target
                        continue
                    if status != 200:
                        code = (
                            "biblioaccess_acsm_denied" if status in (401, 403) else
                            "biblioaccess_acsm_missing" if status == 404 else
                            "biblioaccess_acsm_rate_limited" if status == 429 else
                            "biblioaccess_acsm_remote_error" if status >= 500 else
                            "biblioaccess_acsm_unavailable"
                        )
                        raise ACSMDeliveryError(code)
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0].strip().casefold()
                    )
                    if content_type in {
                        "text/html", "application/xhtml+xml",
                        "application/json", "text/plain",
                    }:
                        raise ACSMDeliveryError(
                            "biblioaccess_acsm_content_type_invalid",
                        )
                    # XML/octet-stream or a missing Content-Type still
                    # requires independent Adobe ACSM structural validation.
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_ACSM_BYTES:
                            raise ACSMDeliveryError("biblioaccess_acsm_body_limit")
                        chunks.append(chunk)
                    break
            else:
                raise ACSMDeliveryError("biblioaccess_acsm_redirect_limit")
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
