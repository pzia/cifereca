"""#3252 Kobo: revalidated selected descriptor -> private Core staging candidate.

Only Core can grant consent, finish ADEPT, verify bytes and publish an OriginLink.
No Vault credential, signed URL, account data or private HTTP response is
included in the returned candidate reference or a provider exception.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from xml.etree import ElementTree
import zipfile

import httpx
from cifereca.plugin_api import (
    Account, HTTPX_TRANSPORT, ProviderCapabilityError, ProviderError,
)
from cifereca_kobo_delivery_offer import exact_kobo_offer_id, _safe_https_get

_MAX_DELIVERY_BYTES = 128 * 1024 * 1024
_MAX_ACSM_BYTES = 2 * 1024 * 1024


def _single_admitted_candidate(payload, offer_id, *, identity_id,
                               credential_id, account_id, product_id):
    """Re-use EXACTLY the existing descriptor classifier, not a parallel right.

    The full envelope is checked first, including all foreign/ambiguous
    siblings. A single surviving candidate must reproduce the full opaque
    offer when evaluated alone. URLs stay in this private, transient function.
    """
    scope = dict(identity_id=identity_id, credential_id=credential_id,
                 account_id=account_id, product_id=product_id)
    if exact_kobo_offer_id(payload, **scope) != offer_id:
        raise ProviderCapabilityError("kobo_candidate_offer_changed")
    acquisition = payload.get("acquisition")
    candidates = acquisition.get("candidates") if type(acquisition) is dict else None
    if type(candidates) is not list:
        raise ProviderCapabilityError("kobo_candidate_descriptor_invalid")
    matches = []
    for candidate in candidates:
        if type(candidate) is not dict or not _safe_https_get(candidate):
            continue
        singleton = dict(payload, acquisition=dict(
            acquisition, candidates=[candidate],
        ))
        if exact_kobo_offer_id(singleton, **scope) == offer_id:
            matches.append(candidate)
    if len(matches) != 1:
        raise ProviderCapabilityError("kobo_candidate_descriptor_ambiguous")
    selected = matches[0]
    fmt = selected.get("media_format") or selected.get("source_format")
    fmt = fmt.strip().lower() if type(fmt) is str else ""
    if (selected.get("kind") == "acsm"
            and selected.get("handling") == "adobe_adept"
            and fmt == "acsm"):
        return selected["url"], "acsm"
    if (selected.get("kind") == "http"
            and selected.get("handling") == "http_plain"
            and fmt in ("epub", "pdf", "kepub")):
        return selected["url"], "epub" if fmt == "kepub" else fmt
    raise ProviderCapabilityError("kobo_candidate_unsupported_route")


def _candidate_format_ok(path: Path, fmt: str) -> bool:
    try:
        with path.open("rb") as data:
            head = data.read(32)
        if fmt == "pdf":
            return head.startswith(b"%PDF-")
        if fmt == "epub":
            return head.startswith(b"PK") and zipfile.is_zipfile(path)
        if fmt == "acsm" and path.stat().st_size <= _MAX_ACSM_BYTES:
            node = ElementTree.parse(path).getroot()
            return (node.tag.rsplit("}", 1)[-1].casefold()
                    == "fulfillmenttoken")
    except (OSError, ElementTree.ParseError, zipfile.BadZipFile, ValueError):
        return False
    return False


def stage_selected_candidate(session, account: Account, *, edition_id,
                             right_id, kind, branch, delivery_offer_id,
                             target_dir, explicit_user_action=False):
    """Exactly one Core-selected purchase, fresh POST, then one isolated GET.

    A signed URL can rotate without changing the offer ID, but the supplier
    must freshly attest the same route, account and product before the GET.
    """
    if (explicit_user_action is not True or type(account) is not Account
            or type(edition_id) is not str or not edition_id
            or type(right_id) is not str or right_id != edition_id
            or kind != "purchase" or branch != "personal_manifest"
            or type(delivery_offer_id) is not str
            or not delivery_offer_id.startswith("kobo.descriptor:")
            or len(delivery_offer_id) != len("kobo.descriptor:") + 64
            or any(c not in "0123456789abcdef" for c in
                   delivery_offer_id[len("kobo.descriptor:"):])
            or account.provider_id != "kobo"
            or account.credential_id != session.credential_id
            or type(account.identity_id) is not str or not account.identity_id):
        raise ProviderCapabilityError("kobo_candidate_not_admitted")
    target = Path(target_dir)
    if (not target.is_dir() or target.is_symlink()
            or not target.resolve(strict=True).is_dir()):
        raise ProviderCapabilityError("kobo_candidate_staging_invalid")

    accounts = session._accounts
    if not session.connected or len(accounts) != 1:
        raise ProviderCapabilityError("kobo_candidate_owner_unverified")
    live = accounts[0]
    broker, client = session.identity_broker, session.client
    epoch = session._personal_binding_generation
    caller = (account.identity_id, account.provider_id,
              account.credential_id, account.unique_id)
    original = (live.identity_id, live.provider_id,
                live.credential_id, live.unique_id)
    owner = session._rights_owner_snapshot(caller[3])
    if (original[1:] != caller[1:] or owner is None
            or owner[1] != (caller[0],)):
        raise ProviderCapabilityError("kobo_candidate_owner_unverified")

    def bound():
        try:
            session._ensure_personal_binding(
                live, epoch, expected_account_id=caller[3],
                expected_identity_id=original[0],
            )
            return (
                session.connected is True
                and session._accounts is accounts
                and len(accounts) == 1 and accounts[0] is live
                and session.client is client
                and session.identity_broker is broker
                and (account.identity_id, account.provider_id,
                     account.credential_id, account.unique_id) == caller
                and (live.identity_id, live.provider_id,
                     live.credential_id, live.unique_id) == original
                and session._rights_owner_snapshot(caller[3]) == owner
            )
        except (ProviderError, AttributeError, TypeError, ValueError):
            return False

    if not bound():
        raise ProviderCapabilityError("kobo_candidate_owner_changed")
    # An opaque offer string alone cannot bootstrap a purchase: recheck the
    # attested personal manifest before initiating another private POST.
    inventory = session.rights_observation_snapshot(account)
    if not bound():
        raise ProviderCapabilityError("kobo_candidate_owner_changed")
    right = inventory.exact(edition_id, right_id, "purchase")
    if (right is None or right.state != "active"
            or right.provider_attested is not True):
        raise ProviderCapabilityError("kobo_candidate_purchase_unobserved")

    payload = session.resolve_acquisition(
        right_id, mode="descriptor", expected_offer_id=delivery_offer_id,
        expected_identity_id=caller[0],
    )
    if not bound():
        raise ProviderCapabilityError("kobo_candidate_owner_changed")
    url, fmt = _single_admitted_candidate(
        payload, delivery_offer_id, identity_id=caller[0],
        credential_id=caller[2], account_id=caller[3],
        product_id=right_id,
    )
    if not bound():
        raise ProviderCapabilityError("kobo_candidate_owner_changed")
    # Always use Core's public-peer pinned transport, never the authenticated
    # Vault client or system proxies. A 302/307 cannot leak a signed URL,
    # cookies or an Authorization header to another origin.
    fd, filename = tempfile.mkstemp(
        prefix=".kobo-selected-", suffix="." + fmt, dir=target,
    )
    path = Path(filename)
    succeeded = False
    try:
        with os.fdopen(fd, "wb") as out:
            if not bound():
                raise ProviderCapabilityError("kobo_candidate_owner_changed")
            with HTTPX_TRANSPORT.public_client(
                producer="provider.kobo.delivery", timeout=30,
            ) as transfer:
                with transfer.stream("GET", url, headers={
                    "Accept": "application/octet-stream",
                }, follow_redirects=False) as response:
                    if response.status_code != 200:
                        raise ProviderCapabilityError(
                            "kobo_candidate_http_unavailable"
                        )
                    declared = response.headers.get("content-length", "")
                    if declared.isdecimal() and int(declared) > _MAX_DELIVERY_BYTES:
                        raise ProviderCapabilityError(
                            "kobo_candidate_size_exceeded"
                        )
                    media = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if media in ("text/html", "text/plain", "application/json"):
                        raise ProviderCapabilityError(
                            "kobo_candidate_not_a_file"
                        )
                    size = 0
                    for chunk in response.iter_bytes():
                        if not bound():
                            raise ProviderCapabilityError(
                                "kobo_candidate_owner_changed"
                            )
                        size += len(chunk)
                        if size > _MAX_DELIVERY_BYTES:
                            raise ProviderCapabilityError(
                                "kobo_candidate_size_exceeded"
                            )
                        out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if (not bound() or not _candidate_format_ok(path, fmt)):
            raise ProviderCapabilityError(
                "kobo_candidate_owner_changed" if not bound()
                else "kobo_candidate_format_invalid"
            )
        # A supplier stream is never a Core receipt or verified OriginLink.
        # Only this staging path is returned; the signed URL remains private.
        succeeded = True
        return path, ""
    except (httpx.HTTPError, OSError, ValueError):
        raise ProviderCapabilityError(
            "kobo_candidate_transport_failed"
        ) from None
    finally:
        if not succeeded:
            path.unlink(missing_ok=True)
