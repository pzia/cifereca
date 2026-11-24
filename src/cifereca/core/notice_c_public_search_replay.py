"""#4982 generic adapter for replay through the established public Search ABI.

Only an explicit trusted composition supplies the retained-input read/hold
and cache-only parser callbacks. Core imports no concrete provider module,
does not discover private methods and does not invent a public declaration.
The provider must return its own same-input PublicSearchNotice envelope.
"""
from __future__ import annotations

from .notice_c_reparse_execution import PublicReplayPort
from .notice_c_work_bridge import UnavailableNoticeWork
from .notice_search_cover_producer import admit_public_search_catalogue
from .search_orchestration import _public_rows_from_response, _verified_public_cover_witness
from .notice_cover_projection import notice_cover_document_ref
from .notice_cover_repository import PersistedNoticeCover
from .notice_source_observation import _id
from ..plugin_api import ProviderSearchResponse


def _anonymous_session(session, provider_id):
    return (getattr(session, "provider_id", None) == provider_id
            and getattr(session, "connected", None) is True
            and getattr(session, "credential_id", None) == "__public__"
            and type(getattr(session, "credentials", None)) is dict
            and not session.credentials
            and type(getattr(session, "_accounts", None)) is list
            and not session._accounts)


def _observations(response, session, *, provider_id, observed_at, allow_coverless=False):
    from .notice_c_raw_input_receipt import _coverless_public_search_observation
    if (not _anonymous_session(session, provider_id)
            or type(response) is not ProviderSearchResponse):
        raise UnavailableNoticeWork("notice_reparse_public_response_required")
    for row in _public_rows_from_response(response, session):
        if not _verified_public_cover_witness(row):
            continue
        # Public source coordinates ignore the viewing profile. This inert
        # marker satisfies the legacy projector's nonempty identity argument;
        # it is never a user identity, private credential or access grant.
        observation = admit_public_search_catalogue(
            row, identity_id="__public__", observed_at=observed_at,
            public_cover_url=row.thumbnail_url,
        )
        if (observation is not None
                and observation.key.visibility == "public"
                and observation.key.provider_id == provider_id
                and observation.parser_version == "2"
                and (observation.status == "complete"
                     or (allow_coverless is True
                         and _coverless_public_search_observation(observation)))):
            yield observation


def public_replay_same_call_observation(response, session, source):
    """Check a trusted producer's exact original response against its C source.

    The caller must independently bind the retained bytes/token to THIS
    response (not a previously cached Search DTO). This projection is not
    proof of that binding or authorization to record a raw-input receipt.
    """
    if type(source) is not PersistedNoticeCover:
        raise UnavailableNoticeWork("notice_reparse_invalid_observation")
    matches = [
        item for item in _observations(
            response, session, provider_id=source.observation.key.provider_id,
            observed_at=source.observation.observed_at,
            allow_coverless=source.observation.status == "partial",
        ) if item.key == source.observation.key
    ]
    if len(matches) != 1 or matches[0] != source.observation:
        raise UnavailableNoticeWork("notice_reparse_original_response_changed")
    return matches[0]


def make_public_search_replay_port(
    session, *, provider_id, parser_family_digest, implementation_digest,
    read_input, reparse_response, hold_input, authorize_capture_input=None,
):
    """Build a registered Core adapter; all callbacks are cache-only.

    The trusted composition is responsible for selecting a live anonymous
    session, normalizing its private cache receipt to ReplayInput, mapping
    its closed busy status to RetryableNoticeWork and holding the exact
    producer lease during final publication. No named provider dispatch.
    """
    if (not _id(provider_id) or not _anonymous_session(session, provider_id)
            or not all(callable(fn) for fn in (read_input, reparse_response, hold_input))
            or (authorize_capture_input is not None and not callable(authorize_capture_input))):
        raise ValueError("notice_reparse_invalid_public_adapter")

    def parse(request, selected):
        source = request.source
        if (source.observation.key.provider_id != provider_id
                or not _anonymous_session(session, provider_id)):
            raise UnavailableNoticeWork("notice_reparse_public_response_required")
        bio = source.observation.fields_copy()["search_result"]["public_bibliography"]
        record_id = bio["record_id"]
        response = reparse_response(selected.producer_token, record_id)
        # Exactly one parser-owned row and declaration, not a search result
        # selected from another response, record, edition, or account.
        if (type(response) is not ProviderSearchResponse
                or type(response.results) is not list or len(response.results) != 1
                or type(response.public_notices) is not tuple
                or len(response.public_notices) != 1):
            raise UnavailableNoticeWork("notice_reparse_public_response_required")
        matches = tuple(_observations(
            response, session, provider_id=provider_id,
            observed_at=source.observation.observed_at,
            allow_coverless=True,
        ))
        if (len(matches) != 1 or matches[0].key != source.observation.key
                or notice_cover_document_ref(matches[0].key) != source.document):
            raise UnavailableNoticeWork("notice_reparse_invalid_observation")
        return matches[0]

    return PublicReplayPort(
        provider_id, parser_family_digest, implementation_digest,
        read_input, parse, hold_input, authorize_capture_input,
    )


def make_retained_public_replay_port(
    session, *, provider_id, parser_family_digest, implementation_digest,
    read_retained, hold_retained, reparse_response, normalize_input,
    authorize_capture_input=None,
):
    """Compose explicitly supplied cache callbacks without provider discovery.

    The composition owner supplies the normalization and error mapping;
    this adapter never opens a session or starts a transport.
    """
    from contextlib import contextmanager

    if not all(callable(fn) for fn in
               (read_retained, hold_retained, normalize_input)):
        raise ValueError("notice_reparse_invalid_public_adapter")

    def read(token):
        raw, ttl = read_retained(token)
        if raw is None or type(ttl) is not int or ttl < 1:
            return None
        return normalize_input(raw)

    @contextmanager
    def hold(token):
        with hold_retained(token) as raw:
            yield None if raw is None else normalize_input(raw)

    return make_public_search_replay_port(
        session, provider_id=provider_id,
        parser_family_digest=parser_family_digest,
        implementation_digest=implementation_digest,
        read_input=read, hold_input=hold,
        reparse_response=reparse_response,
        authorize_capture_input=authorize_capture_input,
    )
