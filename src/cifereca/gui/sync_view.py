from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping


@dataclass(frozen=True)
class SyncSourcePresentation:
    activation_label: str
    connection_label: str
    sync_label: str
    summary: str
    action_label: str
    action_enabled: bool
    disabled_reason: str


def _timestamp_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        # Provider timestamps can contain private URLs or credential data.
        return ""
    return parsed.strftime("%d/%m/%Y %H:%M")


def sync_identity_action_label(identity: object | None) -> str:
    label = str(getattr(identity, "label", "") or "").strip() if identity is not None else ""
    return f"Synchroniser {label}" if label else "Synchroniser les sources"




def sync_local_preflight_presentation(result: object) -> str:
    """Fixed French labels and bounded counts; never echo provider metadata."""
    facts = getattr(result, "facts", None)
    if not isinstance(facts, Mapping):
        return "Aperçu indisponible. Aucun téléchargement autorisé."
    status = facts.get("status")
    if getattr(result, "ok", False) is not True or status != "preview_only":
        reasons = {
            "source_disabled": "Source désactivée.",
            "not_supported": "Cette source ne propose pas de prévisualisation Kobo.",
            "unknown_source": "Source introuvable.",
            "invalid_source": "Identité ou compte incohérent.",
            "invalid_options": "Paramètres de prévisualisation invalides.",
            "preview_unavailable": "État local indisponible.",
        }
        return (
            reasons.get(status, "Aperçu indisponible.")
            if type(status) is str else "Aperçu indisponible."
        ) + " Aucun téléchargement autorisé."
    if (
        facts.get("acquisition_authorized") is not False
        or facts.get("read_only") is not True
        or facts.get("requires_manifest_validation") is not True
    ):
        return "Aperçu indisponible. Aucun téléchargement autorisé."
    counts = facts.get("counts")
    expected = (
        "already_local_linked", "already_local_verified",
        "needs_reconciliation", "candidate_unverified",
        "potentially_missing", "attention_required", "blocked_scope",
        "blocked_unlinked", "not_downloadable", "invalid_manifest_items",
    )
    known = facts.get("known_item_count")
    if (
        type(known) is not int or not 0 <= known <= 1_000_000
        or not isinstance(counts, Mapping)
        or any(
            type(counts.get(key)) is not int or not 0 <= counts[key] <= 1_000_000
            for key in expected
        )
    ):
        return "Aperçu indisponible. Aucun téléchargement autorisé."
    local = counts["already_local_linked"] + counts["already_local_verified"]
    blockers = sum(counts[key] for key in (
        "attention_required", "blocked_scope", "blocked_unlinked",
        "not_downloadable", "invalid_manifest_items",
    ))
    evidence = (
        "Fichiers vérifiés par empreinte"
        if facts.get("files_verified") is True
        else "État des liens locaux uniquement (sans vérification SHA)"
    )
    return (
        f"Notices connues : {known} · déjà liées : {local} · "
        f"à rapprocher : {counts['needs_reconciliation']} · "
        f"candidats incertains : {counts['candidate_unverified']} · "
        f"potentiellement manquants : {counts['potentially_missing']} · "
        f"actions requises : {blockers}.\n"
        f"{evidence}. Manifeste distant non actualisé ; "
        "aucun téléchargement autorisé depuis cet aperçu."
    )

def _public_sync_provider_slug(source: object) -> str:
    """Bound public plugin ID; never render private connection coordinates."""
    provider = getattr(source, "provider_id", "")
    provider = provider.strip() if type(provider) is str else ""
    if provider and len(provider) <= 64 and all(
        char.isascii() and (char.isalnum() or char in "._-") for char in provider
    ):
        return provider
    return "fournisseur inconnu"


def sync_source_identity_label(source: object, *, disambiguate: bool = True) -> str:
    """Qualify homonymous row titles, preserving unique legacy row titles."""
    provider = _public_sync_provider_slug(source)
    label = getattr(source, "label", "")
    label = label.strip() if type(label) is str else ""
    if not label:
        return provider
    return (
        label if not disambiguate or label.casefold() == provider.casefold()
        else f"{label} · {provider}"
    )


def sync_source_row_labels(sources: object) -> tuple[str, ...]:
    """Give same-name connections distinct *ephemeral* visible titles.

    A source or credential hash must never become a pseudonymous UI identifier.
    Numbering follows the already scoped broker order; it is presentation only
    and does not grant or change synchronization permissions (#980).
    """
    rows = tuple(sources)
    names = [
        str(getattr(source, "label", "") or getattr(source, "provider_id", "") or "").strip()
        for source in rows
    ]
    name_counts: dict[str, int] = {}
    for name in names:
        key = name.casefold()
        name_counts[key] = name_counts.get(key, 0) + 1
    qualified = [
        sync_source_identity_label(
            source, disambiguate=name_counts[name.casefold()] > 1
        )
        for source, name in zip(rows, names)
    ]
    qualified_counts: dict[str, int] = {}
    for name in qualified:
        key = name.casefold()
        qualified_counts[key] = qualified_counts.get(key, 0) + 1
    ordinal: dict[str, int] = {}
    display: list[str] = []
    for name in qualified:
        key = name.casefold()
        count = qualified_counts[key]
        if count > 1:
            ordinal[key] = ordinal.get(key, 0) + 1
            display.append(f"{name} · connexion {ordinal[key]}/{count}")
        else:
            display.append(name)
    return tuple(display)


def sync_source_presentation(source: object) -> SyncSourcePresentation:
    enabled = bool(getattr(source, "enabled", False))
    connected = bool(getattr(source, "connected", False))
    capable = bool(getattr(source, "sync_capable", False))
    # Preserve stable action labels for non-Kobo sources; the GTK row and
    # task label provide the disambiguated provider presentation (#980).
    label = str(getattr(source, "label", "") or getattr(source, "provider_id", "") or "cette source")

    activation = "Activée" if enabled else "Désactivée"
    connection = "Connectée" if connected else "Déconnectée"

    # A provider error is an opaque diagnostic, not safe UI text: it may
    # contain bearer URLs, filesystem paths, or private account identifiers.
    error = bool(getattr(source, "error", ""))
    status = str(getattr(source, "status", "") or "idle").strip().casefold()
    last_success = _timestamp_label(getattr(source, "last_success", ""))
    last_attempt = _timestamp_label(getattr(source, "last_attempt", ""))

    if not capable:
        sync_label = "Non prise en charge"
    elif status == "running":
        sync_label = "En cours…"
    elif status == "partial":
        sync_label = "Partielle" + (f" · {last_attempt}" if last_attempt else "")
    elif error or status in {"error", "failed"}:
        sync_label = "Échec"
    elif last_success:
        sync_label = f"Dernier succès {last_success}"
    elif last_attempt:
        sync_label = f"Dernier essai {last_attempt}"
    else:
        sync_label = "Jamais synchronisée"

    action_enabled = enabled and connected and capable
    # Capability is intrinsic to the source: disabling an unsupported
    # public catalogue must not suggest that enabling it would allow sync.
    if not capable:
        disabled_reason = "Cette source ne fournit pas de synchronisation de données."
    elif not enabled:
        disabled_reason = "Activez cette source pour la synchroniser."
    elif not connected:
        disabled_reason = "Reconnectez ce compte avant de synchroniser."
    else:
        disabled_reason = ""

    summary = (
        f"Fournisseur : {_public_sync_provider_slug(source)}\n"
        f"Activation : {activation} · Connexion : {connection}\n"
        f"Synchronisation : {sync_label}"
    )
    # Explicit scope, independent of provider-supplied free-form metadata.
    # A manual source sync is not the global opening/acquisition workflow.
    if capable:
        capabilities = getattr(source, "sync_capabilities", ())
        if not isinstance(capabilities, (tuple, list, frozenset)):
            capabilities = ()
        phases = []
        if "remote_inventory" in capabilities:
            phases.append("inventaire distant")
        if "entitlements" in capabilities:
            phases.append("droits et emprunts")
        if phases:
            summary += "\nPortée de cette source : " + ", ".join(phases) + "."
        else:
            summary += "\nPortée de cette source : données en ligne prises en charge."
        summary += "\nOpération ciblée : ne lance pas la synchronisation globale au démarrage."
    else:
        summary += "\nCatalogue public consultable indépendamment de la synchronisation."
    # A Kobo source refresh only updates online metadata. It is deliberately
    # NOT equivalent to the separate opt-in Kobo backup/acquisition operation.
    kobo_notices_only = getattr(source, "provider_id", "") == "kobo"
    if kobo_notices_only:
        summary += "\nActualise uniquement les notices Kobo en ligne ; ne télécharge aucun ebook."
        if "local_materialization" in getattr(source, "sync_capabilities", ()):
            summary += (
                "\nAcquisition locale Kobo disponible sur commande dédiée ; "
                "cette synchronisation ne la lance jamais."
            )
        else:
            summary += "\nAcquisition locale Kobo : non prise en charge par ce compte."
    if status in {"partial", "error", "failed"} and last_success:
        summary += f"\nDernier succès confirmé : {last_success}"
    if not action_enabled:
        summary += f"\nAction indisponible : {disabled_reason}"

    return SyncSourcePresentation(
        activation_label=activation,
        connection_label=connection,
        sync_label=sync_label,
        summary=summary,
        action_label=("Mettre à jour les notices Kobo en ligne" if kobo_notices_only
                      else f"Synchroniser {label}"),
        action_enabled=action_enabled,
        disabled_reason=disabled_reason,
    )
