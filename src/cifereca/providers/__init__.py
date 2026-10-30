"""Provider plugin API and internal registry.

0.3.x deliberately keeps plugins inside the source tree while the contract is
stabilised. The registry is already independent from concrete providers so the
same modules can later be moved to package entry points without changing the
GUI or service layer.
"""
from .base import (
    AccountOptionField,
    CredentialField,
    ProviderCapabilities,
    ProviderSearchCapabilities,
    ProviderCapabilityError,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderPlugin,
    ProviderSession,
)
from .registry import ProviderRegistry

__all__ = [
    "AccountOptionField",
    "CredentialField",
    "ProviderCapabilities",
    "ProviderSearchCapabilities",
    "ProviderCapabilityError",
    "ProviderConfigurationError",
    "ProviderDescriptor",
    "ProviderPlugin",
    "ProviderRegistry",
    "ProviderSession",
]
