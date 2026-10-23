"""Manual ACSM import orchestration behind the LibraryService facade.

The facade retains its dedicated import lock and action recording. Its historical
filesystem, hash, profile-error and artifact-inspection seams are injected at
call time. Keep staging, original preservation, runtime contract, validation,
indexing and ledger ordering unchanged; no background acquisition is introduced.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from .bnf_pret_saved_token import inspect_saved_bnf_token


def import_acsm(
    self, source: Path, *, identity_id: str | None = None, provider_id: str = "manual", title: str | None = None,
    Path, ProviderConfigurationError, sha256_file, hashlib, data_dir, shutil,
    detect_acsm_format, AdeptProfileResolutionError, inspect_artifact, os,
) -> dict[str, Any]:
    """Import a user ACSM without deleting the original file.

    The ACSM is copied into resumable staging, fulfilled through the DRM
    plugin layer, validated, then indexed as a normal local Artifact.
    """
    original_source = Path(source).expanduser()
    # BnF's consented browser download may use the neutral .bin suffix.
    # Admit this path only after bounded, namespace-specific XML inspection
    # on the *original* path, before resolving symlinks, hashing, creating
    # any staging area, ledger entry, or invoking ADEPT. An ACSM candidate
    # remains untrusted: the existing identity-scoped ADEPT broker decides.
    is_bin = original_source.suffix.casefold() == ".bin"
    if is_bin:
        evidence = inspect_saved_bnf_token(original_source)
        if evidence.code != "candidate_acsm":
            raise ProviderConfigurationError(
                "Jeton ADEPT .bin invalide, expiré ou non vérifiable"
            )
    source = original_source.resolve()
    if not source.is_file() or source.suffix.casefold() not in (
        {".acsm", ".bin"} if is_bin else {".acsm"}
    ):
        raise ProviderConfigurationError("Sélectionnez un fichier ACSM valide")
    identity = self.identity_by_id(identity_id) if identity_id else self.current_identity
    if identity is None:
        raise ProviderConfigurationError("Aucune Identity active pour l'import ACSM")
    source_sha = sha256_file(source)
    acquisition_id = f"manual-acsm:{hashlib.sha256((identity.identity_id + ':' + source_sha).encode()).hexdigest()[:32]}"
    existing = self.storage.acquisition_get(acquisition_id)
    if existing and existing.get("status") == "complete":
        path = Path(str(dict(existing.get("metadata") or {}).get("path") or ""))
        if path.is_file():
            return {"status": "unchanged", "path": str(path), "acquisition_id": acquisition_id}

    work = data_dir() / "acquisition-work" / identity.identity_id / "manual" / source_sha[:16]
    work.mkdir(parents=True, exist_ok=True)
    staged = work / "source.acsm"
    if work.is_symlink() or staged.is_symlink():
        raise ProviderConfigurationError("Zone de reprise ACSM ambiguë")
    if not staged.exists() or sha256_file(staged) != source_sha:
        shutil.copy2(source, staged)
    if sha256_file(staged) != source_sha:
        raise ProviderConfigurationError("Le fichier ACSM a changé pendant l’import")
    if is_bin and inspect_saved_bnf_token(staged).code != "candidate_acsm":
        # Revalidate the *copied* bytes, not only the external source read
        # before hash/copy. A concurrent replacement may never cross the
        # trusted staging -> ADEPT boundary.
        raise ProviderConfigurationError(
            "Jeton ADEPT .bin invalide, expiré ou non vérifiable"
        )
    self.storage.acquisition_put({
        "acquisition_id": acquisition_id, "provider_id": provider_id, "credential_id": "manual",
        "identity_id": identity.identity_id, "item_id": source_sha, "title": title or source.stem,
        "status": "acquiring", "strategy": "manual_acsm", "executor": "drm.ade",
        "metadata": {"source_acsm_sha256": source_sha, "staging": str(staged)},
    })
    try:
        media_format = detect_acsm_format(staged) or "epub"
        suffix = ".pdf" if media_format == "pdf" else ".epub"
        # A title is presentation, never an artifact identity. Keep each
        # ACSM's outputs in its own namespace and refuse unindexed residue.
        root = self.library_identity_provider_dir(identity.identity_id, provider_id)
        destination_dir = root / ("acsm-" + source_sha)
        if destination_dir.is_symlink() or (destination_dir.exists() and not destination_dir.is_dir()):
            raise ProviderConfigurationError("Dossier d’import ACSM ambigu")
        destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        filename = self._loan_destination_name(title or source.stem, suffix)
        destination = destination_dir / filename
        if any(destination_dir.iterdir()):
            raise ProviderConfigurationError("Un artefact ACSM non attesté existe déjà ; réconciliation nécessaire")
        try:
            profile_name, _profile_source = self.resolve_adept_profile_for_identity(identity.identity_id)
        except AdeptProfileResolutionError as exc:
            # Profile resolver errors are not evidence safe for persistent
            # diagnostics. Keep only the two known Core remediation codes.
            code = (exc.code if type(exc.code) is str and exc.code in {
                "adept_profile_required", "adept_profile_ambiguous",
            } else "adept_profile_required")
            self.storage.acquisition_update_state(
                acquisition_id, "attention_required", error_code=code,
                error_message="Profil ADEPT indisponible",
                attention_code=code, metadata={"staging": str(staged)},
            )
            return {"status": "attention_required", "code": code, "staging": str(staged)}
        request = {
            "schema_version": 2,
            "source": {"type": "acsm", "provider": provider_id, "path": str(staged), "headers": {}},
            "destination": str(destination), "format": media_format, "profile": profile_name,
            "metadata": {"title": title or source.stem, "manual_import": True, "identity_id": identity.identity_id},
            "options": {"force": False},
        }
        originating_profile_id = self._originating_adept_profile_id(identity.identity_id, profile_name)
        result = self.execute_acquisition_request(request)
        from ..adept_binding import capture_binding
        adept_binding = capture_binding(result, profile_name=profile_name, profile_id=originating_profile_id)
        artifact = result.get("artifact") if isinstance(result, dict) else None
        produced = Path(str((artifact or {}).get("path") or destination))
        if produced.is_symlink() or produced.resolve() != destination.resolve():
            raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
        if not produced.is_file():
            raise ProviderConfigurationError("Le runtime DRM n'a produit aucun Artifact")
        inspected = inspect_artifact(produced)
        if not inspected.valid or inspected.media_format not in {"epub", "pdf"}:
            raise ProviderConfigurationError("Artifact ACSM invalide")
        expected_suffix = ".pdf" if inspected.media_format == "pdf" else ".epub"
        if produced.suffix.casefold() != expected_suffix:
            corrected = produced.with_suffix(expected_suffix)
            # link() atomically refuses any existing destination, including
            # a dangling symlink. Never unlink another local document.
            os.link(produced, corrected)
            produced.unlink()
            produced = corrected
        indexed = self.catalog_index_path(
            produced, title=title or source.stem, item_id=source_sha,
            identity_id=identity.identity_id, provider_id=provider_id,
        )
        if indexed.get("status") != "indexed":
            raise ProviderConfigurationError("Indexation ACSM impossible")
        self.storage.acquisition_update_state(
            acquisition_id, "complete", artifact_id=indexed.get("artifact_id"),
            metadata={"path": str(produced), "catalog_index": indexed, "source_acsm_sha256": source_sha, "adept_binding": adept_binding},
        )
        try:
            staged.unlink(missing_ok=True)
            if not any(work.iterdir()):
                work.rmdir()
        except OSError:
            pass
        return {"status": "complete", "path": str(produced), "artifact_id": indexed.get("artifact_id"), "acquisition_id": acquisition_id}
    except Exception:
        current = self.storage.acquisition_get(acquisition_id)
        if not current or current.get("status") != "attention_required":
            # Never persist a runtime/provider/SQLite exception's class or
            # text; both may contain ACSM payloads, signed URLs or paths.
            self.storage.acquisition_update_state(
                acquisition_id, "retryable_error",
                error_code="manual_acsm_failed", error_message="Import ACSM échoué",
                metadata={"staging": str(staged), "source_acsm_sha256": source_sha},
            )
        raise

