"""#4982 declared public Search refresh adapter, independent of provider name.

The trusted composition must bind the retained token to the original exact
same-call response and qualify the provider's transport reservation/start
and publication guards. No discovery, callback introspection, URL dispatch,
plugin ABI change or automatic registration takes place in Core.
"""
from __future__ import annotations

from .notice_c_fetch_authority import PublicFetchAuthority
from .notice_c_fetch_execution import PublicFetchPort, ReservedPublicFetch
from .notice_c_public_search_replay import _anonymous_session, _observations
from .notice_c_raw_input_receipt import (
    PublicRawInputReceipt, _valid, read_public_raw_input_receipt,
)
from .notice_c_work_bridge import UnavailableNoticeWork
from .notice_cover_repository import PersistedNoticeCover
from .notice_cover_projection import notice_cover_document_ref
from ..plugin_api import ProviderSearchResponse


def make_public_search_fetch_port(
    authority, session, source, receipt, *, reserve_exact, clock,
):
    """Bind one declared transport to a previously admitted source and receipt.

    The opaque producer token lives in the existing TTL receipt, never a
    durable job or a URL. A qualified adapter supplies reserve_exact with
    the established supplier budget, late-start and session guard contract.
    This factory is an internal composition helper, not a public grant API.
    """
    if (type(authority) is not PublicFetchAuthority
            or type(source) is not PersistedNoticeCover
            or type(receipt) is not PublicRawInputReceipt or not _valid(receipt)
            or receipt.producer_token is None
            or not callable(clock) or not callable(reserve_exact)):
        raise ValueError("notice_fetch_invalid_public_adapter")
    provider_id = source.observation.key.provider_id
    if (not _anonymous_session(session, provider_id)
            or source.observation.key.visibility != "public"
            or source.observation.parser_version != "2"
            or source.observation.status != "complete"
            or read_public_raw_input_receipt(
                authority._storage, source.document, now=clock(),
            ) != receipt):
        raise ValueError("notice_fetch_invalid_public_adapter")

    original_key = source.observation.key
    record_id = source.observation.fields_copy()["search_result"]["public_bibliography"]["record_id"]

    def reserve(permit):
        # No Storage access here: called under the Core dispatch guard,
        # and the supplier reservation acquires its own nonblocking locks.
        if (permit.source != source
                or permit.transport_digest != receipt.transport_digest
                or permit.implementation_digest != receipt.parser_implementation_digest
                or not _anonymous_session(session, provider_id)):
            raise UnavailableNoticeWork("notice_fetch_original_source_changed")
        provider = reserve_exact(
            receipt.producer_token, record_id, receipt.transport_digest,
            hold_start_authority=lambda: authority.transport_start_guard(permit),
        )
        if provider is None:
            return None

        def execute(control):
            response, observed_at = provider.execute(control)
            # A declaration in a same-call response is not authorization.
            # Admit one exact public descriptive record using the same
            # projector as Search, reject partial/foreign/multiple records.
            if (type(response) is not ProviderSearchResponse
                    or response.partial is not False
                    or type(response.results) is not list
                    or len(response.results) != 1
                    or type(response.public_notices) is not tuple
                    or len(response.public_notices) != 1
                    or type(observed_at) is not int
                    or not permit.admitted_at <= observed_at <= clock()):
                raise UnavailableNoticeWork("notice_fetch_invalid_public_response")
            matches = tuple(_observations(
                response, session, provider_id=provider_id, observed_at=observed_at,
            ))
            if (len(matches) != 1 or matches[0].key != original_key
                    or notice_cover_document_ref(matches[0].key) != source.document):
                raise UnavailableNoticeWork("notice_fetch_invalid_public_response")
            return matches[0]

        return ReservedPublicFetch(
            permit, execute, provider.release, provider.hold_publication,
        )

    return PublicFetchPort(
        provider_id, receipt.transport_digest,
        receipt.parser_implementation_digest, reserve,
    )
