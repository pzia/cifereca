from __future__ import annotations
import hashlib
import json
import os
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any
from .paths import config_file
from .config_defaults import DEFAULT_CONFIG, DEFAULT_REQUEST_TIMEOUT_SECONDS




def _identity_directory_slug(value: str, fallback: str = "identity") -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip(".-_").lower()
    return text or fallback

def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or config_file()
        self.data = deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = _merge(DEFAULT_CONFIG, raw)
                    # One-time in-memory bridge from 0.2.x: carry the remembered
                    # Paris username into the first provider account. The
                    # password was never stored by 0.2.x and must be entered
                    # once in Preferences.
                    if not self.get("providers.accounts", []) and str(self.get("username", "")).strip():
                        self.set("providers.accounts", [{
                            "id": "paris-default",
                            "provider": "paris",
                            "label": "Bibliothèques de Paris",
                            "enabled": True,
                            "credentials": {
                                "username": str(self.get("username", "")).strip(),
                                "password": "",
                            },
                            "options": ({"base_url": str(self.get("base_url"))} if self.get("base_url") else {}),
                        }])
            except Exception:
                self.data = deepcopy(DEFAULT_CONFIG)
            finally:
                # Provider credentials are intentionally local/plaintext in
                # 0.3.x; tighten an existing file as soon as it is opened.
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass

        # 0.3.1: Gallica devient un provider anonyme prêt. L'ajout est fait
        # une seule fois dans l'état de configuration, y compris pour une
        # installation neuve. Il sera persisté au prochain save().
        if not bool(self.get("providers.gallica_migration_031", False)):
            rows = self.provider_accounts()
            if not any(str(x.get("provider") or "").lower() == "gallica" for x in rows):
                rows.append({
                    "id": "gallica-public",
                    "provider": "gallica",
                    "label": "Gallica",
                    "enabled": True,
                    "anonymous": True,
                    "credentials": {},
                    "options": {},  # Gallica plugin owns its OPDS endpoint default.
                })
                self.set("providers.accounts", rows)
            self.set("providers.gallica_migration_031", True)

        # 0.8.1: identities become first-class. Existing provider connections are
        # attached conservatively to one stable default identity; no filesystem
        # move is performed by this configuration migration.
        identities = self.identities()
        if not identities:
            identities = [{
                "id": "default",
                "label": "Ma bibliothèque",
                "preferences": {"prefer_original_language": False, "reading_languages": ["fr"]},
            }]
            self.set("identities", identities)
        # 0.8.1 filesystem naming: identity_id stays an immutable technical key;
        # directory_name is a human, stable and independently editable slug.
        used_directories: set[str] = set()
        directory_changed = False
        for identity in identities:
            if not isinstance(identity, dict):
                continue
            fallback = _identity_directory_slug(str(identity.get("id") or "identity"), "identity")
            requested = str(identity.get("directory_name") or "").strip()
            candidate = _identity_directory_slug(requested or str(identity.get("label") or identity.get("id") or "identity"), fallback)
            base = candidate
            serial = 2
            while candidate in used_directories:
                candidate = f"{base}-{serial}"
                serial += 1
            used_directories.add(candidate)
            if identity.get("directory_name") != candidate:
                identity["directory_name"] = candidate
                directory_changed = True

        default_identity = str(identities[0].get("id") or "default")
        rows = self.provider_accounts()
        changed = directory_changed
        for row in rows:
            if not str(row.get("identity_id") or "").strip():
                row["identity_id"] = default_identity
                changed = True
        # 0.8.1 cardinality repair: one Kobo Vault connection per Paris
        # identity. Early builds attached all legacy provider rows to the first
        # identity. Split only the extra Kobo rows into deterministic identities;
        # do not guess or duplicate ADE profile bindings.
        seen_kobo: dict[str, str] = {}
        identity_by_id = {str(item.get("id") or ""): item for item in identities if isinstance(item, dict)}
        for row in rows:
            if str(row.get("provider") or "").strip().lower() != "kobo":
                continue
            credential_id = str(row.get("id") or "").strip()
            identity_id = str(row.get("identity_id") or default_identity).strip() or default_identity
            previous = seen_kobo.get(identity_id)
            if not previous or previous == credential_id:
                seen_kobo[identity_id] = credential_id
                continue
            suffix = hashlib.sha256(credential_id.encode("utf-8", "surrogatepass")).hexdigest()[:8]
            base_id = f"{identity_id}-kobo-{suffix}"
            derived_id = base_id
            serial = 2
            while derived_id in identity_by_id:
                derived_id = f"{base_id}-{serial}"
                serial += 1
            source = identity_by_id.get(identity_id, {})
            derived_label = str(row.get("label") or f"{source.get('label') or identity_id} Kobo")
            directory_base = _identity_directory_slug(derived_label, derived_id)
            directory_name = directory_base
            serial_dir = 2
            while directory_name in used_directories:
                directory_name = f"{directory_base}-{serial_dir}"
                serial_dir += 1
            used_directories.add(directory_name)
            derived = {
                "id": derived_id,
                "label": derived_label,
                "directory_name": directory_name,
                "preferences": deepcopy(source.get("preferences") or {
                    "prefer_original_language": False, "reading_languages": ["fr"]
                }),
            }
            identities.append(derived)
            identity_by_id[derived_id] = derived
            row["identity_id"] = derived_id
            seen_kobo[derived_id] = credential_id
            changed = True

        # An early 0.8.1 config could also repeat the same ADE profile on several
        # identities. Keep the first occurrence; later identities remain unbound
        # so the application can ask for an explicit profile instead of guessing.
        seen_profiles: set[str] = set()
        for identity in identities:
            if not isinstance(identity, dict):
                continue
            profile = str(identity.get("adept_profile") or "").strip()
            if not profile:
                continue
            if profile in seen_profiles:
                identity.pop("adept_profile", None)
                changed = True
            else:
                seen_profiles.add(profile)

        if changed:
            self.set("identities", identities)
            self.set("providers.accounts", rows)

    def save(self) -> None:
        # Contract versions are discovered from Kobo-Adept at runtime and must
        # never become user configuration again.
        kobo_cfg = self.data.get("kobo_adept")
        if isinstance(kobo_cfg, dict):
            kobo_cfg.pop("contract_version", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    def identities(self) -> list[dict[str, Any]]:
        rows = self.get("identities", [])
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity_id = str(row.get("id") or "").strip()
            if not identity_id:
                continue
            value = deepcopy(row)
            value["id"] = identity_id
            value["label"] = str(value.get("label") or identity_id).strip() or identity_id
            value["directory_name"] = _identity_directory_slug(
                str(value.get("directory_name") or value["label"]),
                _identity_directory_slug(identity_id, "identity"),
            )
            prefs = value.get("preferences")
            if not isinstance(prefs, dict):
                prefs = {}
            langs = prefs.get("reading_languages", ["fr"])
            if not isinstance(langs, list):
                langs = [langs]
            prefs["reading_languages"] = [str(x).strip().lower() for x in langs if str(x).strip()] or ["fr"]
            prefs["prefer_original_language"] = bool(prefs.get("prefer_original_language", False))
            value["preferences"] = prefs
            out.append(value)
        return out

    def identity(self, identity_id: str) -> dict[str, Any] | None:
        return next((row for row in self.identities() if row.get("id") == identity_id), None)

    def upsert_identity(self, identity: dict[str, Any]) -> None:
        identity_id = str(identity.get("id") or "").strip()
        if not identity_id:
            raise ValueError("identity requires id")
        value = deepcopy(identity); value["id"] = identity_id
        rows = self.identities()
        for index, row in enumerate(rows):
            if str(row.get("id")) == identity_id:
                rows[index] = value
                break
        else:
            rows.append(value)
        self.set("identities", rows)

    def provider_accounts(self, provider_id: str | None = None, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        rows = self.get("providers.accounts", [])
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if provider_id and str(row.get("provider", "")).lower() != provider_id.lower():
                continue
            if enabled_only and not bool(row.get("enabled", True)):
                continue
            out.append(deepcopy(row))
        return out

    def upsert_provider_account(self, account: dict[str, Any]) -> None:
        provider = str(account.get("provider") or "").strip().lower()
        account_id = str(account.get("id") or "").strip()
        if not provider or not account_id:
            raise ValueError("provider account requires provider and id")
        account = deepcopy(account)
        account["provider"] = provider
        rows = self.provider_accounts()
        for idx, row in enumerate(rows):
            if str(row.get("provider", "")).lower() == provider and str(row.get("id", "")) == account_id:
                rows[idx] = account
                break
        else:
            rows.append(account)
        self.set("providers.accounts", rows)

    def remove_provider_account(self, provider_id: str, account_id: str) -> None:
        rows = [
            row for row in self.provider_accounts()
            if not (str(row.get("provider", "")).lower() == provider_id.lower() and str(row.get("id", "")) == account_id)
        ]
        self.set("providers.accounts", rows)


    def provider_account(self, provider_id: str, credential_id: str | None = None) -> dict[str, Any] | None:
        provider_id = str(provider_id or "").strip().lower()
        credential_id = str(credential_id or "").strip()
        for row in self.provider_accounts(provider_id):
            if credential_id and str(row.get("id") or "").strip() != credential_id:
                continue
            return row
        return None

    def provider_option(
        self, provider_id: str, key: str, default: Any = None, *, credential_id: str | None = None
    ) -> Any:
        row = self.provider_account(provider_id, credential_id)
        options = row.get("options") if isinstance(row, dict) else None
        if isinstance(options, dict) and key in options:
            return options[key]
        return default

    def paris_base_url(self, credential_id: str | None = None) -> str:
        value = self.provider_option("paris", "base_url", None, credential_id=credential_id)
        if value is None:
            value = self.get("base_url", "")
        return str(value or "").strip()

    def request_timeout_seconds(self, provider_id: str | None = None, credential_id: str | None = None) -> float:
        value: Any = None
        if provider_id:
            value = self.provider_option(provider_id, "request_timeout_seconds", None, credential_id=credential_id)
            if value is None and str(provider_id).lower() == "kobo":
                value = self.provider_option(provider_id, "timeout_seconds", None, credential_id=credential_id)
        if value is None:
            value = self.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(DEFAULT_REQUEST_TIMEOUT_SECONDS)

    def provider_settings(self, provider_id: str) -> dict[str, Any]:
        value = self.get(f"providers.settings.{provider_id}", {})
        return deepcopy(value) if isinstance(value, dict) else {}

    def set_provider_settings(self, provider_id: str, value: dict[str, Any]) -> None:
        provider_id = str(provider_id or "").strip().lower()
        if not provider_id:
            raise ValueError("provider_id required")
        self.set(f"providers.settings.{provider_id}", deepcopy(value))

    def has_configured_provider(self) -> bool:
        for row in self.provider_accounts(enabled_only=True):
            credentials = row.get("credentials") or {}
            if isinstance(credentials, dict) and any(str(v or "").strip() for v in credentials.values()):
                return True
            # Anonymous providers may later be enabled with no credentials.
            if bool(row.get("anonymous", False)):
                return True
        return False

