"""Paris #3118: explicit Core claim before any potentially mutating hop."""
from ..models import Account, SearchResult
from .manual_library_borrow import manual_borrow, manual_borrow_available


def manual_paris_borrow_available(service, item: SearchResult) -> bool:
    return manual_borrow_available(
        service, item, provider_id="paris", docbase="DILICOM", primary_id="")


def manual_paris_borrow(
    service, item: SearchResult, *, expected_identity_id: str,
    confirmed: bool = False, expected_context_epoch: int | None = None,
    expected_account: Account | None = None,
) -> dict[str, object]:
    return manual_borrow(
        service, item, provider_id="paris", docbase="DILICOM", primary_id="",
        expected_identity_id=expected_identity_id, confirmed=confirmed,
        expected_context_epoch=expected_context_epoch,
        expected_account=expected_account,
    )


__all__ = ["manual_paris_borrow", "manual_paris_borrow_available"]
