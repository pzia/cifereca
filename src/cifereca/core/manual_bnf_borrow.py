"""Compatibility entry points for the existing explicit BnF borrow contract."""
from ..models import Account, SearchResult
from .manual_library_borrow import manual_borrow, manual_borrow_available


def manual_bnf_borrow_available(service, item: SearchResult) -> bool:
    return manual_borrow_available(
        service, item, provider_id="bnf-pret", docbase="BNF_PRET")


def manual_bnf_borrow(
    service, item: SearchResult, *, expected_identity_id: str,
    confirmed: bool = False, site_checked: bool = False,
    expected_context_epoch: int | None = None,
    expected_account: Account | None = None,
) -> dict[str, object]:
    return manual_borrow(
        service, item, provider_id="bnf-pret", docbase="BNF_PRET",
        expected_identity_id=expected_identity_id, confirmed=confirmed,
        site_checked=site_checked, expected_context_epoch=expected_context_epoch,
        expected_account=expected_account,
    )


__all__ = ["manual_bnf_borrow", "manual_bnf_borrow_available"]
