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
    # Broker timestamps with UTC offsets are instants. Show them in the
    # machine's current local timezone rather than silently displaying UTC as
    # wall time (e.g. a 2-hour error in Paris in summer). For naive timestamps,
    # the timezone is unknown: preserve the supplied wall time, do not
    # silently assume UTC, a provider timezone or the account location.
    if parsed.utcoffset() is not None:
        parsed = parsed.astimezone()
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
    # An offline backup-only preview has no verified current Kobo rights
    # manifest. Personal user_declarations are tracked separately: they are
    # not receipts or backup links, and must NOT be counted as zero merely
    # because this preview did not inspect their exact right/file pairs.
    declarations = counts.get("already_local_declared")
    if declarations is not None and (
        type(declarations) is not int or not 0 <= declarations <= 1_000_000
        or facts.get("files_verified") is not True
        or facts.get("manifest_refreshed") is not True
    ):
        return "Aperçu indisponible. Aucun téléchargement autorisé."
    blockers = sum(counts[key] for key in (
        "attention_required", "blocked_scope", "blocked_unlinked",
        "not_downloadable", "invalid_manifest_items",
    ))
    evidence = (
        "Fichiers vérifiés par empreinte"
        if facts.get("files_verified") is True
        else "État des liens locaux uniquement (sans vérification SHA)"
    )
    declaration_label = (
        f"Déclarations manuelles vérifiées pour ce compte : {declarations}."
        if declarations is not None else
        "Déclarations manuelles : non évaluées dans cet aperçu. "
        "Une déclaration enregistrée n'est ni un reçu Kobo ni un fichier "
        "vérifié ; le compteur de liens n'en atteste pas l'absence."
    )
    manifest_label = (
        "Manifeste distant actualisé ; " if facts.get("manifest_refreshed") is True
        else "Manifeste distant non actualisé ; "
    )
    return (
        f"Notices connues : {known} · liens de sauvegarde repérés : {local} · "
        f"à rapprocher : {counts['needs_reconciliation']} · "
        f"candidats incertains : {counts['candidate_unverified']} · "
        f"potentiellement manquants : {counts['potentially_missing']} · "
        f"actions requises : {blockers}.\n"
        f"{declaration_label}\n"
        f"{evidence}. {manifest_label}"
        "aucun téléchargement autorisé depuis cet aperçu."
    )

def sync_numilog_local_preview_presentation(result: object) -> str:
    """Project only the offline Numilog broker's bounded, read-only facts."""
    unavailable = "Aperçu local indisponible. Aucun téléchargement autorisé."
    facts = getattr(result, "facts", None)
    if not isinstance(facts, Mapping) or getattr(result, "ok", None) is not True:
        return unavailable
    if (
        facts.get("status") != "preview_only"
        or facts.get("phase") != "local_materialization"
        or facts.get("read_only") is not True
        or facts.get("offline") is not True
        or facts.get("acquisition_authorized") is not False
        or facts.get("confirmation_available") is not False
        or facts.get("schedule_available") is not False
        or facts.get("manifest_refreshed") is not False
        or facts.get("requires_manifest_validation") is not True
        or type(facts.get("files_verified")) is not bool
    ):
        return unavailable
    counts = facts.get("counts")
    required = (
        "owned_purchases", "already_local_linked", "already_local_verified",
        "needs_file_verification", "potentially_missing", "candidate_unverified",
        "needs_reconciliation", "attention_required", "not_downloadable",
        "ignored_unattested_rights",
    )
    if (not isinstance(counts, Mapping)
            or any(type(counts.get(key)) is not int
                   or not 0 <= counts[key] <= 1_000_000 for key in required)
            or type(facts.get("known_item_count")) is not int
            or facts["known_item_count"] != counts["owned_purchases"]):
        return unavailable
    evidence = (
        "Vérification SHA demandée"
        if facts["files_verified"] else "Sans lecture ni vérification SHA des fichiers"
    )
    return (
        f"Achats connus : {counts['owned_purchases']} · "
        f"liens locaux : {counts['already_local_linked']} · "
        f"fichiers vérifiés : {counts['already_local_verified']} · "
        f"à vérifier : {counts['needs_file_verification']} · "
        f"potentiellement manquants : {counts['potentially_missing']} · "
        f"candidats incertains : {counts['candidate_unverified']} · "
        f"à rapprocher : {counts['needs_reconciliation']} · "
        f"actions requises : {counts['attention_required']} · "
        f"non téléchargeables : {counts['not_downloadable']}.\n"
        f"{evidence}. Inventaire distant non actualisé ; "
        "aucun téléchargement autorisé depuis cet aperçu."
    )


def sync_local_reconciliation_presentation(result: object) -> str:
    """Safe UI text derived only from fixed, bounded audit counters.

    Never echo a provider failure, persisted path, case payload or SHA. An
    audit is proof of local bytes only: it grants no acquisition/repair rights.
    """
    facts = getattr(result, "facts", None)
    if not isinstance(facts, Mapping):
        return "Audit local indisponible. Aucun fichier modifié."
    status = facts.get("status")
    if getattr(result, "ok", False) is not True or status != "audit_only":
        reasons = {
            "unknown_source": "Source introuvable.",
            "not_supported": "Audit réservé aux sources Kobo.",
            "invalid_source": "Liaison identité-compte incohérente.",
            "ambiguous_source": "Plusieurs identités revendiquent ce compte : audit bloqué.",
            "audit_unavailable": "Audit local indisponible.",
        }
        return (
            reasons.get(status, "Audit local indisponible.")
            if type(status) is str else "Audit local indisponible."
        ) + " Aucun fichier modifié."
    if (facts.get("read_only") is not True
            or facts.get("offline") is not True
            or facts.get("acquisition_authorized") is not False
            or type(facts.get("truncated")) is not bool):
        return "Audit local indisponible. Aucun fichier modifié."
    allowed = (
        "backup_rows", "linked_files_verified", "unverified_placements",
        "missing_or_unlinked", "stored_hash_mismatch",
        "unproven_catalogue_links", "same_product_different_bytes",
        "binary_match_groups", "active_entitlements_unmaterialized",
        "unindexed_regular_file_candidates", "identity_flat_file_candidates",
    )
    counts = facts.get("counts")
    if (not isinstance(counts, Mapping)
            or any(type(counts.get(key)) is not int or
                   not 0 <= counts[key] <= 1_000_000 for key in allowed)):
        return "Audit local indisponible. Aucun fichier modifié."
    completeness = (
        "Audit partiel : limites d’inspection atteintes."
        if facts["truncated"] else "Audit terminé dans les limites prévues."
    )
    # The Core audit has three *shared* hard budgets. Expose them only when
    # all four typed counters are present and consistent. Never infer WHICH
    # budget was exhausted, and never use arbitrary provider limitations.
    budget_fields = (
        "files_hashed_limit", "directory_entries_limit",
        "total_hash_bytes_limit", "total_hash_bytes_read",
    )
    values = tuple(facts.get(key) for key in budget_fields)
    budget_label = ""
    if (
        all(type(value) is int for value in values)
        and 1 <= values[0] <= 100_000
        and 1 <= values[1] <= 1_000_000
        and 1 <= values[2] <= 1 << 40
        and 0 <= values[3] <= values[2]
    ):
        # Integer MiB avoids locale-dependent formatting and private paths.
        used_mib = values[3] // (1024 * 1024)
        limit_mib = values[2] // (1024 * 1024)
        budget_label = (
            f"Plafonds partagés : {values[0]} vérifications SHA max. · "
            f"{values[1]} entrées max. · "
            f"{used_mib}/{limit_mib} Mio de lecture cumulée. "
        )
    # The Core broker may include a closed explanation of a partial audit.
    # Never interpolate arbitrary reason strings or partially shaped fields.
    # Legacy audits with no reason tuple keep the original generic message.
    known_truncation_reasons = {
        "record_budget": "plafond de lignes persistées",
        "backup_records_budget": "plafond de lignes de sauvegarde Kobo",
        "catalogue_rows_budget": "plafond de lignes du catalogue Kobo",
        "active_entitlements_budget": "plafond de lignes de droits Kobo actifs",
        "hash_attempt_budget": "plafond de tentatives SHA",
        "hash_byte_budget": "budget d’octets de lecture SHA",
        "product_path_budget": "plus de huit chemins candidats par produit",
        "directory_entry_budget": "plafond d’entrées de répertoire",
        "directory_scan_unavailable": "inspection d’un répertoire indisponible",
    }
    reason_keys = (
        "truncation_reasons", "hash_attempts_used",
        "directory_entries_inspected",
    )
    reason_label = ""
    reason_present = [key in facts for key in reason_keys]
    if any(reason_present):
        reasons, attempts, inspected = (facts.get(key) for key in reason_keys)
        if (
            not all(reason_present) or not budget_label
            or type(reasons) is not list or len(reasons) > len(known_truncation_reasons)
            or any(type(reason) is not str or reason not in known_truncation_reasons
                   for reason in reasons)
            or reasons != sorted(set(reasons))
            or bool(reasons) != facts["truncated"]
            or type(attempts) is not int or not 0 <= attempts <= values[0]
            or type(inspected) is not int or not 0 <= inspected <= values[1]
        ):
            return "Audit local indisponible. Aucun fichier modifié."
        if reasons:
            reason_label = "Causes attestées : " + ", ".join(
                known_truncation_reasons[reason] for reason in reasons
            ) + ". "
            if any(reason in {
                "backup_records_budget", "catalogue_rows_budget",
                "active_entitlements_budget",
            } for reason in reasons):
                reason_label += (
                    "Plafond sur les lignes persistées du périmètre choisi "
                    "(historique et doublons possibles), pas sur les livres distincts. "
                )
        budget_label += (
            f"Tentatives SHA : {attempts}/{values[0]} · "
            f"entrées inspectées : {inspected}/{values[1]}. "
        )
    return (
        f"Fichiers locaux vérifiés par SHA : {counts['linked_files_verified']} · "
        f"groupes de doublons binaires : {counts['binary_match_groups']} · "
        f"empreintes divergentes : {counts['stored_hash_mismatch']}.\n"
        f"Fichiers non indexés candidats : {counts['unindexed_regular_file_candidates']} · "
        f"placements incertains : {counts['unverified_placements']} · "
        f"droits sans artefact confirmé : {counts['active_entitlements_unmaterialized']}.\n"
        f"{completeness} {reason_label}{budget_label}"
        "Aucune réconciliation, acquisition ni suppression autorisée."
    )


def sync_source_phase_summary(source: object) -> str:
    """Present only Core-enumerated phase states, never provider diagnostics.

    Inventory/rights and local acquisition have different success clocks.
    A partial acquisition must not inherit an inventory success timestamp.
    """
    entries = getattr(source, "phase_states", ())
    if not isinstance(entries, (tuple, list)) or len(entries) > 8:
        return ""
    known = {
        "idle": "Jamais exécutée", "running": "En cours",
        "success": "Réussie", "partial": "Partielle",
        "failed": "Échec", "cancelled": "Annulée",
        "blocked": "Bloquée", "attention_required": "Action nécessaire",
        "interrupted": "Interrompue", "unknown": "État inconnu",
        "not_supported": "Non prise en charge",
        "materialization_failed": "Indisponible",
        "source_changed": "Source modifiée",
        "source_disabled": "Source désactivée",
        "source_disconnected": "Source déconnectée",
    }
    captions = (
        ("discover_rights", "Notices et droits"),
        ("materialize", "Téléchargement local"),
    )
    parts = []
    for phase, heading in captions:
        row = next((
            value for value in entries
            if getattr(value, "phase", None) == phase
        ), None)
        if row is None:
            continue
        raw_status = getattr(row, "status", None)
        status = known.get(raw_status if type(raw_status) is str else None, "État inconnu")
        # An unsupported phase must not be rewritten based on a supplier ID.
        # The capability projection is the only available generic evidence.
        sync_caps = getattr(source, "sync_capabilities", ())
        if (phase == "materialize" and raw_status == "not_supported"
                and isinstance(sync_caps, (tuple, list, frozenset))
                and len(sync_caps) <= 32
                and "loan_auto_materialization" in sync_caps):
            status = "Téléchargement distinct de cette synchronisation"
        attempt = _timestamp_label(getattr(row, "last_attempt", ""))
        success = _timestamp_label(getattr(row, "last_success", ""))
        detail = heading + " : " + status
        if attempt:
            detail += " · dernier essai " + attempt
        if success:
            detail += " · dernier succès confirmé " + success
        parts.append(detail)
    return "\n".join(parts)


def _public_sync_provider_slug(source: object) -> str:
    """Bound public plugin ID; never render private connection coordinates."""
    provider = getattr(source, "provider_id", "")
    provider = provider.strip() if type(provider) is str else ""
    if provider and len(provider) <= 64 and all(
        char.isascii() and (char.isalnum() or char in "._-") for char in provider
    ):
        return provider
    return "fournisseur inconnu"


def _safe_sync_source_label(source: object) -> str:
    """Bound display-only broker labels; never echo URLs or opaque secrets.

    Account routing and consent always use opaque Core source IDs separately.
    A malformed label must not reach task names, GTK widgets or diagnostics.
    """
    provider = _public_sync_provider_slug(source)
    label = getattr(source, "label", None)
    if type(label) is not str:
        return provider
    label = label.strip()
    if (not 1 <= len(label) <= 96 or not label.isprintable()
            or "://" in label or label.startswith(("/", "\\", "file:"))
            # A provider-supplied account label may accidentally contain
            # auth headers rather than a signed URL. Never display credentials
            # in GTK rows, accessible names, task labels or exported task logs.
            or any(marker in label.casefold() for marker in (
                "?token=", "&token=", "?key=", "&key=",
                "?signature=", "&signature=", "access_token=",
                "authorization:", "proxy-authorization:", "bearer ", "basic ",
                "cookie:", "set-cookie:", "x-api-key:", "api_key=",
                "api-key=", "password=", "password:", "client_secret=",
                "secret=", "refresh_token=", "session_token=",
            ))):
        return provider
    return label


def sync_source_identity_label(source: object, *, disambiguate: bool = True) -> str:
    """Qualify homonymous row titles, preserving unique legacy row titles."""
    provider = _public_sync_provider_slug(source)
    label = _safe_sync_source_label(source)
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
        _safe_sync_source_label(source)
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


def sync_source_informational_partial(source: object) -> bool:
    """Exact Core partial-coverage marker + positive, separately known local phase.

    An incomplete remote inventory is not evidence of a failed GET. Do not
    downgrade an unknown/failed local phase, an opaque error, or a partial
    result with insufficient phase evidence. No provider ID is consulted.
    """
    if (getattr(source, "sync_capable", None) is not True
            or getattr(source, "status", None) != "partial"
            or getattr(source, "error", None) != "Synchronisation partielle"):
        return False
    entries = getattr(source, "phase_states", ())
    if not isinstance(entries, (tuple, list)) or len(entries) != 2:
        return False
    phases: dict[str, str] = {}
    for row in entries:
        phase = getattr(row, "phase", None)
        status = getattr(row, "status", None)
        if (type(phase) is not str or phase not in {"discover_rights", "materialize"}
                or type(status) is not str or phase in phases):
            return False
        phases[phase] = status
    return (phases.get("discover_rights") == "partial"
            and phases.get("materialize") in {"success", "not_supported"})


def sync_source_attention(source: object) -> tuple[str, str] | None:
    """Expose the most severe closed-state alert, never a provider payload.

    A warning in a local phase must not hide an error in inventory or in the
    source-wide status. Within the same severity, prefer local materialization,
    then inventory/rights, then generic sync and disconnection. This projection
    is presentation-only: it grants no acquisition or synchronization rights.
    """
    if getattr(source, "sync_capable", None) is not True:
        return None
    # Each candidate is a fixed UI label. Phase and status values are used
    # solely as dictionary keys; arbitrary upstream data never becomes text.
    candidates: list[tuple[str, str]] = []
    information_only = sync_source_informational_partial(source)
    entries = getattr(source, "phase_states", ())
    if isinstance(entries, (tuple, list)) and len(entries) <= 8:
        phase_labels = (
            ("materialize", "Téléchargement local"),
            ("discover_rights", "Notices et droits"),
        )
        words = {
            "failed": ("error", "échec — action nécessaire"),
            "materialization_failed": ("error", "échec — action nécessaire"),
            "blocked": ("error", "bloqué — action nécessaire"),
            "attention_required": ("warning", "action nécessaire"),
            "interrupted": ("warning", "interrompu"),
            "partial": ("warning", "résultat partiel"),
        }
        for phase, label in phase_labels:
            for row in entries:
                if getattr(row, "phase", None) != phase:
                    continue
                raw = getattr(row, "status", None)
                issue = words.get(raw) if type(raw) is str else None
                if issue:
                    if information_only and phase == "discover_rights" and raw == "partial":
                        candidates.append(("info", "Notices et droits : couverture partielle (information)"))
                    else:
                        candidates.append((issue[0], f"{label} : {issue[1]}"))
    raw_status = getattr(source, "status", None)
    status = raw_status.strip().casefold() if type(raw_status) is str and len(raw_status) <= 64 else ""
    raw_error = getattr(source, "error", None)
    # Provider errors are opaque; never display their text, paths or URLs.
    error_present = type(raw_error) is str and bool(raw_error)
    # The Core broker marks a legitimately partial loan inventory as
    # status=partial with its own fixed compatibility text in error. This
    # marker is not a provider exception: treating any nonempty error as a
    # fatal alert mislabels positive BnF/Paris loans with unknown coverage.
    # Keep unknown/private errors severe, including under partial status.
    core_partial_marker = status == "partial" and raw_error == "Synchronisation partielle"
    if status in {"error", "failed"} or (error_present and not core_partial_marker):
        candidates.append(("error", "Synchronisation : échec — action nécessaire"))
    elif status in {"blocked", "attention_required"}:
        candidates.append(("warning", "Synchronisation : action nécessaire"))
    elif status == "partial":
        candidates.append((
            ("info", "Synchronisation : couverture partielle (information)")
            if information_only
            else ("warning", "Synchronisation : résultat partiel")
        ))
    if getattr(source, "enabled", None) is True and getattr(source, "connected", None) is False:
        candidates.append(("warning", "Compte déconnecté — reconnexion nécessaire"))
    # Stable precedence independent of upstream phase ordering. A severe
    # source-wide failure must win over an earlier local partial warning.
    for severity in ("error", "warning", "info"):
        for candidate in candidates:
            if candidate[0] == severity:
                return candidate
    return None


def sync_source_presentation(source: object, *, canonical: bool = False) -> SyncSourcePresentation:
    enabled = bool(getattr(source, "enabled", False))
    connected = bool(getattr(source, "connected", False))
    capable = bool(getattr(source, "sync_capable", False))
    # Preserve stable action labels for non-Kobo sources; the GTK row and
    # task label provide the disambiguated provider presentation (#980).
    label = _safe_sync_source_label(source)

    activation = "Activée" if enabled else "Désactivée"
    connection = "Connectée" if connected else "Déconnectée"

    # Use the same closed, phase-aware severity projection for the primary
    # status text and its attention icon. A failed local GET/placement phase
    # must never sit under a misleading "Dernier succès" headline.
    alert = sync_source_attention(source) if capable else None
    status = str(getattr(source, "status", "") or "idle").strip().casefold()
    last_success = _timestamp_label(getattr(source, "last_success", ""))
    last_attempt = _timestamp_label(getattr(source, "last_attempt", ""))

    if not capable:
        sync_label = "Non prise en charge"
    elif alert is not None and alert[0] == "error":
        # Applies to source-wide failures AND failed/blocked phase projections.
        # The fixed Core inventory-partial marker remains informational if the
        # independent local phase has no observed failure.
        sync_label = "Échec"
    elif status == "running":
        sync_label = "En cours…"
    elif status == "partial":
        sync_label = "Partielle" + (f" · {last_attempt}" if last_attempt else "")
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
    phase_summary = sync_source_phase_summary(source)
    if phase_summary:
        summary += "\n" + phase_summary
    if sync_source_informational_partial(source):
        summary += (
            "\nCouverture incomplète des notices et droits ; "
            "aucun échec de téléchargement local déclaré. "
            "La présence et la publication des fichiers restent distinctes."
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
            phases.append("observations de droits et prêts")
        if "local_materialization" in capabilities:
            phases.append("acquisition locale selon autorisation")
        if phases:
            summary += "\nPortée de cette source : " + ", ".join(phases) + "."
        else:
            summary += "\nPortée de cette source : données en ligne prises en charge."
        summary += "\nOpération ciblée : ne lance pas la synchronisation globale au démarrage."
    else:
        # A non-sync-capable Account can be a catalogue search, a technical
        # source or a disabled plugin. Search capability != refresh capability;
        # neither is proof that this is a public catalogue.
        summary += (
            "\nCette source ne fournit pas d’inventaire synchronisable. "
            "Une recherche de catalogue, si disponible, est une opération distincte."
        )
    if canonical and capable:
        summary += "\nLes fichiers disponibles sont reçus selon l’autorisation de ce compte et les capacités de la source."
    if (status in {"partial", "error", "failed"}
            or alert is not None and alert[0] == "error") and last_success:
        summary += f"\nDernier succès confirmé : {last_success}"
    if not action_enabled:
        summary += f"\nAction indisponible : {disabled_reason}"

    return SyncSourcePresentation(
        activation_label=activation,
        connection_label=connection,
        sync_label=sync_label,
        summary=summary,
        action_label=f"Synchroniser {label}",
        action_enabled=action_enabled,
        disabled_reason=disabled_reason,
    )
