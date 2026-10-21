"""Private, offline and strictly non-authorizing Numilog pair preflight (#2714).

This is NOT a repair proposal or transaction permit. Evidence belongs to one
revocable local inspection session and must be recollected under a Core lock
before any later write. Do not serialize, diagnose or expose raw SHA/account/
paths through this private API.
"""
from __future__ import annotations

from dataclasses import dataclass

_REASON_LABELS = {
    "selection_unavailable": "Sélection privée indisponible ou périmée.",
    "audit_incomplete": "L’analyse locale est incomplète ; aucune association définitive n’est possible.",
    "right_not_attested": "Droit Numilog actif et authentifié non établi pour cette identité.",
    "right_scope_ambiguous": "Plusieurs droits ou coordonnées de compte incompatibles pour cette édition.",
    "file_unverified": "SHA, structure ou CRC du fichier local non vérifié.",
    "acquisition_missing": "Aucun reçu d’acquisition complet lié à ce compte et à cette édition.",
    "acquisition_conflict": "Des acquisitions incompatibles ont été trouvées ; vérifier la provenance.",
    "acquisition_ambiguous": "Plusieurs acquisitions compatibles : preuve ambiguë.",
    "acquisition_incomplete": "Reçu d’acquisition incomplet ou non publié.",
    "artifact_unavailable": "Artefact du reçu absent, ambigu ou non vérifiable.",
    "artifact_mismatch": "Les octets du fichier ne concordent pas avec l’artefact du reçu.",
    "bibliographic_hint_only": "Une notice/ISBN identique est une simple piste, jamais une preuve d’achat.",
    "transaction_unavailable": "Aucune écriture autorisée : transaction et revalidation à construire séparément.",
}


@dataclass(frozen=True, repr=False)
class ManualLinkPreflight:
    """Only closed diagnostics for local human review, NEVER an action token."""

    status: str
    codes: tuple[str, ...]
    read_only: bool = True
    attachment_authorized: bool = False
    requires_fresh_validation: bool = True

    @property
    def explanation(self) -> str:
        return "\n".join("• " + _REASON_LABELS[code] for code in self.codes)


def _strict_signature(value: object) -> tuple[str, int] | None:
    if not isinstance(value, tuple) or len(value) != 2:
        return None
    digest, size = value
    if (type(digest) is not str or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or type(size) is not int or size <= 0):
        return None
    return digest, size


def review_local_pair(
    right: object, file: object, *, audit_complete: bool,
    bibliographic_hint: bool = False,
) -> ManualLinkPreflight:
    """Pure closed report. All inputs are bounded session-local summaries.

    A valid ledger artifact and identical verified octets are still only a
    *reviewable candidate*; a future read-write authority must verify fresh
    account, ledger, inode, digest, placement and transaction independently.
    """
    if type(right) is not dict or type(file) is not dict:
        return ManualLinkPreflight("unavailable", ("selection_unavailable",))
    blockers: set[str] = set()
    missing: set[str] = set()
    if audit_complete is not True:
        blockers.add("audit_incomplete")
    if right.get("attested") is not True:
        blockers.add("right_not_attested")
    if right.get("ambiguous") is True:
        blockers.add("right_scope_ambiguous")
    if file.get("verified") is not True or _strict_signature(file.get("signature")) is None:
        blockers.add("file_unverified")
    if right.get("ledger_conflict") is True:
        blockers.add("acquisition_conflict")
    ledgers = right.get("ledger_count")
    if type(ledgers) is not int or ledgers < 0 or ledgers > 100_000:
        blockers.add("acquisition_ambiguous")
    elif ledgers == 0:
        missing.add("acquisition_missing")
    elif ledgers != 1:
        blockers.add("acquisition_ambiguous")
    elif right.get("ledger_complete") is not True:
        blockers.add("acquisition_incomplete")
    signature = _strict_signature(right.get("signature"))
    if ledgers == 1 and right.get("ledger_complete") is True:
        if signature is None:
            blockers.add("artifact_unavailable")
        elif _strict_signature(file.get("signature")) is not None and (
            signature != _strict_signature(file.get("signature"))
        ):
            blockers.add("artifact_mismatch")
    if bibliographic_hint is True:
        missing.add("bibliographic_hint_only")
    # Never promise a write even when all private observations agree.
    if blockers:
        status = "blocked"
    elif missing and missing != {"bibliographic_hint_only"}:
        status = "insufficient_evidence"
    else:
        status = "eligible_for_fresh_validation"
    reasons = tuple(sorted(blockers | missing | {"transaction_unavailable"}))
    return ManualLinkPreflight(status, reasons)
