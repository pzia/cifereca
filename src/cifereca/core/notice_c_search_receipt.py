"""Bind a committed public Search source to its still-live producer input."""
from hashlib import sha256
import sqlite3
from time import monotonic, time

from ..plugin_api import ProviderError
from .notice_c_raw_input_receipt import record_public_raw_input_receipt


def record_search_receipt(storage, row, source, retained, *, now):
    """Optional cache-only receipt; failure cannot undo accepted Search."""
    session, token = retained
    # Only a live anonymous session may attest retained bytes.
    if getattr(session, "connected", None) is not True:
        return None
    evidence = getattr(row, "_core_notice_parse_input", None)
    if (type(evidence) is not tuple or len(evidence) != 7
            or evidence[1:5] != (
                source.observation.key, source.observation.raw_json,
                source.observation.parser_id, source.observation.parser_version)
            or session.provider_id != source.observation.key.provider_id
            or getattr(session, "credential_id", None) != "__public__"
            or getattr(session, "credentials", None) != {}
            or getattr(session, "_accounts", None) != []):
        return None
    try:
        started = monotonic()
        raw, ttl = session._read_retained_public_input(token)
        if raw is None or type(ttl) is not int or ttl < 1:
            return None
        with session._hold_retained_public_input(token) as held:
            if (getattr(session, "connected", None) is not True
                    or held is None or held != raw or held.token != token
                    or type(held._body) is not bytes
                    or sha256(held._body).hexdigest() != evidence[5]
                    or held.parser_implementation_digest != evidence[6]):
                return None
            remaining = int(ttl - (monotonic() - started))
            if remaining < 1:
                return None
            return record_public_raw_input_receipt(
                storage, source, held._body,
                transport_digest=held.transport_digest,
                parser_family_digest=held.parser_family_digest,
                parser_implementation_digest=held.parser_implementation_digest,
                now=max(now, int(time())), ttl_seconds=remaining,
                producer_token=token,
            )
    except (ProviderError, ValueError, TypeError, AttributeError, OSError, sqlite3.Error):
        return None
