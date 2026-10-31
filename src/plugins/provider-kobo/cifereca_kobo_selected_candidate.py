"""#3252 Kobo: revalidated selected descriptor -> private Core staging candidate.

Only Core can grant consent, finish ADEPT, verify bytes and publish an OriginLink.
No Vault credential, signed URL, account data or private HTTP response is
included in the returned candidate reference or a provider exception.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
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
    try:
        if (not target.is_dir() or target.is_symlink()
                or target.resolve(strict=True) != target.absolute()):
            raise ProviderCapabilityError("kobo_candidate_staging_invalid")
        initially = os.stat(target, follow_symlinks=False)
        stagefd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(stagefd)
        if (initially.st_dev, initially.st_ino) != (opened.st_dev, opened.st_ino):
            os.close(stagefd)
            raise ProviderCapabilityError("kobo_candidate_staging_invalid")
    except (OSError, ValueError, RuntimeError):
        raise ProviderCapabilityError("kobo_candidate_staging_invalid") from None

    def stage_current():
        try:
            current = os.stat(target, follow_symlinks=False)
            opened = os.fstat(stagefd)
            return (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
        except OSError:
            return False

    try:
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
        if (original[1:] != caller[1:]
                or original[0] not in (None, "", "default", caller[0])
                or owner is None or owner[1] != (caller[0],)):
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
        # An opaque offer string alone cannot bootstrap a file: recheck the
        # exact scoped personal manifest before initiating another private POST.
        inventory = session.rights_observation_snapshot(account)
        if not bound():
            raise ProviderCapabilityError("kobo_candidate_owner_changed")
        right = inventory.exact(edition_id, right_id, "purchase")
        if (right is None or right.provider_attested is not True):
            raise ProviderCapabilityError("kobo_candidate_purchase_unobserved")
        if right.delivery == "unavailable":
            raise ProviderCapabilityError("kobo_candidate_delivery_unavailable")

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
        # The single Core attested-delivery transport owns all supplier
        # redirects, pinned public TCP peers, staging integrity and limits.
        # Vault's authenticated client and POSTs never enter this path.
        if not stage_current():
            raise ProviderCapabilityError("kobo_candidate_staging_rebound")
        candidate = target / (".kobo-selected-" + secrets.token_hex(12) + "." + fmt)
        staged = False
        accepted = False
        try:
            if not bound():
                raise ProviderCapabilityError("kobo_candidate_owner_changed")
            receipt = HTTPX_TRANSPORT.attested_https_download(
                producer="provider.kobo.delivery", url=url, target=candidate,
                admission_current=lambda: bound() and stage_current(),
                headers={"Accept": "application/octet-stream"},
                timeout=30, max_bytes=_MAX_DELIVERY_BYTES, max_redirects=8,
            )
            staged = True
            if not bound():
                raise ProviderCapabilityError("kobo_candidate_owner_changed")
            if not stage_current():
                raise ProviderCapabilityError("kobo_candidate_staging_rebound")
            if not _candidate_format_ok(receipt.path, fmt):
                raise ProviderCapabilityError("kobo_candidate_format_invalid")
            # A staged file is not a Core OriginLink/Placement or consent grant.
            accepted = True
            return receipt.path, ""
        except ValueError as exc:
            code = str(exc)
            if code == "delivery_admission_changed":
                raise ProviderCapabilityError(
                    "kobo_candidate_owner_changed" if not bound()
                    else "kobo_candidate_staging_rebound" if not stage_current()
                    else "kobo_candidate_admission_changed"
                ) from None
            known = {
                "delivery_redirect_loop": "kobo_candidate_redirect_loop",
                "delivery_redirect_limit": "kobo_candidate_redirect_limit",
                "delivery_redirect_invalid": "kobo_candidate_redirect_refused",
                "delivery_redirect_missing": "kobo_candidate_redirect_refused",
                "delivery_url_invalid": "kobo_candidate_redirect_refused",
                "delivery_ip_literal_refused": "kobo_candidate_redirect_refused",
                "delivery_size_exceeded": "kobo_candidate_size_exceeded",
                "delivery_html_instead_of_file": "kobo_candidate_not_a_file",
                "delivery_http_status": "kobo_candidate_http_unavailable",
            }
            raise ProviderCapabilityError(
                known.get(code, "kobo_candidate_transport_failed")
            ) from None
        except (httpx.HTTPError, OSError):
            raise ProviderCapabilityError("kobo_candidate_transport_failed") from None
        finally:
            if staged and not accepted:
                # Core has already cleaned unsuccessful streams. Remove a
                # successfully staged but provider-rejected format only inside
                # the original Core-created directory inode.
                try:
                    os.unlink(candidate.name, dir_fd=stagefd)
                except OSError:
                    pass
    finally:
        os.close(stagefd)
