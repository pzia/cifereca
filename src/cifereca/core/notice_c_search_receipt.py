"""Bind a committed public Search source to its still-live producer input."""
from hashlib import sha256
import sqlite3
from time import monotonic, time
from math import ceil

from ..plugin_api import (
    ProviderError, PublicSearchReplayBusy, PublicSearchReplayCapability,
    PublicSearchReplayInput,
)
from .notice_c_raw_input_receipt import record_public_raw_input_receipt
from .search_orchestration import _CORE_PUBLIC_COVER_WITNESS


def record_search_receipt(storage, row, source, retained, *, now):
    """Optional cache-only receipt; failure cannot undo accepted Search."""
    session, token = retained
    # Only a live anonymous session may attest retained bytes.
    if getattr(session, "connected", None) is not True:
        return None
    evidence = getattr(row, "_core_notice_parse_input", None)
    if (type(evidence) is not tuple or len(evidence) != 7
            or evidence[0] is not _CORE_PUBLIC_COVER_WITNESS
            or evidence[1:5] != (
                source.observation.key, source.observation.raw_json,
                source.observation.parser_id, source.observation.parser_version)
            or session.provider_id != source.observation.key.provider_id
            or getattr(session, "credential_id", None) != "__public__"
            or type(getattr(session, "credentials", None)) is not dict
            or session.credentials
            or type(getattr(session, "_accounts", None)) is not list
            or session._accounts):
        return None
    try:
        started = monotonic()
        capability = session.public_search_replay_capability()
        if (type(capability) is not PublicSearchReplayCapability
                or type(capability.schema_version) is not int
                or capability.schema_version != 1):
            return None
        raw, ttl = capability.read(token)
        if type(raw) is not PublicSearchReplayInput or type(ttl) is not int or ttl < 1:
            return None
        with capability.hold(token) as held:
            if (getattr(session, "connected", None) is not True
                    or type(held) is not PublicSearchReplayInput or held != raw or held.token != token
                    or type(held.body) is not bytes
                    or sha256(held.body).hexdigest() != evidence[5]
                    or held.parser_implementation_digest != evidence[6]):
                return None
            remaining = ttl - ceil(monotonic() - started)
            if remaining < 1:
                return None
            return record_public_raw_input_receipt(
                storage, source, held.body,
                transport_digest=held.transport_digest,
                parser_family_digest=held.parser_family_digest,
                parser_implementation_digest=held.parser_implementation_digest,
                now=max(now, int(time())), ttl_seconds=remaining,
                producer_token=token,
            )
    except (ProviderError, PublicSearchReplayBusy, ValueError, TypeError, AttributeError, OSError, sqlite3.Error):
        return None
