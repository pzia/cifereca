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


def stage_loan_acsm(url: str, target_dir: Path, *, transport=None) -> Path:
    """Direct HTTPS GET of the observed signed offer; no replay restrictions.

    Every call fetches the current link. Existing files do not suppress a
    requested re-download, and the caller's source session owns the identity.
    """
    if type(url) is not str or len(url) > 8192:
        raise ACSMDeliveryError("biblioaccess_acsm_url_invalid")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https"
                or parts.hostname != "entrepot2r.numilog.com"
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.fragment
                or parts.path != "/fulfillment/URLLink.acsm"
                or not parts.query):
            raise ACSMDeliveryError("biblioaccess_acsm_url_invalid")
    except ValueError:
        raise ACSMDeliveryError("biblioaccess_acsm_url_invalid") from None
    if not isinstance(target_dir, Path):
        raise ACSMDeliveryError("biblioaccess_acsm_destination_invalid")

    client = HTTPX_TRANSPORT.client(
        producer="provider.biblioaccess.acsm",
        timeout=httpx.Timeout(25, connect=8),
        follow_redirects=False, trust_env=False, transport=transport,
    )
    try:
        try:
            with client.stream(
                "GET", url, follow_redirects=False,
                headers={"Accept": "application/vnd.adobe.adept+xml, application/xml, */*"},
            ) as response:
                if response.status_code != 200:
                    raise ACSMDeliveryError("biblioaccess_acsm_unavailable")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_ACSM_BYTES:
                        raise ACSMDeliveryError("biblioaccess_acsm_body_limit")
                    chunks.append(chunk)
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
