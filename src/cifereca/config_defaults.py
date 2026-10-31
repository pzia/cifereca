from __future__ import annotations

from typing import Any

DEFAULT_REQUEST_TIMEOUT_SECONDS = 25

# Canonical defaults. A few root-level compatibility keys are intentionally
# retained because Config still reads pre-provider configuration files. New
# configuration examples should place provider endpoints in
# providers.accounts[].options rather than duplicating them here.
DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "",  # Legacy value only; Paris plugin owns the default endpoint.
    "request_timeout_seconds": DEFAULT_REQUEST_TIMEOUT_SECONDS,
    "remember_username": True,
    "username": "",
    "identities": [
        {
            "id": "default",
            "label": "Ma bibliothèque",
            "directory_name": "ma-bibliotheque",
            "preferences": {
                "prefer_original_language": False,
                "reading_languages": ["fr"],
            },
        }
    ],
    "providers": {
        "accounts": [],
        "settings": {},
        "gallica_migration_031": False,
    },
    "downloads": {
        "directory": "",
        "content_type_directories": {"ebook": "", "audiobook": ""},
        "command": [],
        "post_download_command": [],
    },
    "cache": {
        "dashboard_seconds": 300,
        "history_seconds": 1800,
        "search_seconds": 21600,
        "holdings_seconds": 300,
    },
    "diagnostics": {"enabled": True, "level": "normal"},
    "security": {"python_dependency_quarantine_hours": 72},
    "ui": {"width": 1320, "height": 840},
    "experimental_actions": False,
    # LLM is never started and no book data can leave the device by default.
    "llm": {
        "enabled": False,
        "provider": "openai-compatible",
        "endpoint": "",
        "model": "",
        "allow_remote": False,
        "api_key_env": "",
        "timeout_seconds": 5,
        "max_input_chars": 4000,
        "max_output_tokens": 256,
    },
    "acquisition": {"acsm_retention_days": 7},
    "library": {"directory": "~/EBooks"},
    "kobo_backup": {
        "enabled": False,
        "interval_minutes": 60,
        "directory": "",
        "format": "epub",
        "display_profile": "Android",
    },
}
