"""P0 #3867: Core never invents supplier URLs for public catalogue actions.

No provider network, no actual accounts. Source of URL is descriptor,
per-credential options or explicitly configured generic fallback.
"""
from types import SimpleNamespace as NS

from cifereca.services import LibraryService


class Config:
    def __init__(self, *, options=None, paris="", generic=""):
        self.options = options or {}
        self.paris = paris
        self.generic = generic

    def paris_base_url(self, credential_id):
        return self.paris

    def provider_option(self, provider_id, key, default, *, credential_id=None):
        return self.options.get((provider_id, key, credential_id), default)

    def get(self, key, default):
        if key == "library.public_catalogue_fallback":
            return self.generic
        return default


def _host(provider, homepage=None, config=None):
    return NS(
        config=config or Config(),
        provider_descriptors=lambda: [
            NS(id=provider, homepage=homepage),
        ] if homepage else [],
    )


def test_kobo_descriptor_supplies_homepage_without_core_vendor_default():
    host = _host("kobo", homepage="https://catalogue.provider.example/")
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "kobo", "source_credential": "alice"},
    ) == ("catalogue", "https://catalogue.provider.example/")


def test_kobo_explicit_credential_option_overrides_descriptor_but_not_rights():
    config = Config(options={
        ("kobo", "catalogue_url", "alice"): "https://regional.provider.example/",
    })
    host = _host(
        "kobo", homepage="https://global.provider.example/", config=config,
    )
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "kobo", "source_credential": "alice"},
    ) == ("catalogue", "https://regional.provider.example/")
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "kobo", "source_credential": "bob"},
    ) == ("catalogue", "https://global.provider.example/")


def test_paris_plugin_homepage_is_preserved_when_legacy_base_is_missing():
    host = _host("paris", "https://public.paris.example/")
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "paris", "source_credential": "alice"},
    ) == ("catalogue", "https://public.paris.example/")


def test_configured_paris_endpoint_still_resolves_relative_notice():
    host = _host("paris", "https://old.example/", config=Config(
        paris="https://configured.paris.example/",
    ))
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "paris", "friendly_url": "/notice/42"},
    ) == ("notice", "https://configured.paris.example/notice/42")


def test_unconfigured_unknown_source_does_not_open_an_unrelated_vendor():
    host = _host("unknown")
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "unknown"},
    ) is None
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "kobo"},
    ) is None


def test_explicit_generic_fallback_is_used_only_as_catalogue_not_notice():
    host = _host("unknown", config=Config(
        generic="https://chosen.directory.example/",
    ))
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "unknown", "friendly_url": ""},
    ) == ("catalogue", "https://chosen.directory.example/")


def test_configured_supplier_delivery_url_cannot_become_catalogue_fallback():
    host = _host("kobo", config=Config(
        options={
            ("kobo", "catalogue_url", "alice"):
                "https://cdn.example/book.acsm?token=private",
        },
    ))
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "kobo", "source_credential": "alice"},
    ) is None



def test_synthetic_provider_explicit_credential_catalogue_option():
    config = Config(options={
        ("synthetic-provider", "catalogue_url", "alice"):
            "https://alice.synthetic.example/",
    })
    host = _host(
        "synthetic-provider", homepage="https://public.synthetic.example/",
        config=config,
    )
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "synthetic-provider", "source_credential": "alice"},
    ) == ("catalogue", "https://alice.synthetic.example/")
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "synthetic-provider", "source_credential": "bob"},
    ) == ("catalogue", "https://public.synthetic.example/")


def test_paris_legacy_explicit_base_precedes_generic_catalogue_option():
    config = Config(
        paris="https://legacy.paris.example/",
        options={("paris", "catalogue_url", "alice"):
                 "https://configured.paris.example/"},
    )
    host = _host("paris", "https://provider.paris.example/", config=config)
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "paris", "source_credential": "alice",
               "friendly_url": "/notice/42"},
    ) == ("notice", "https://legacy.paris.example/notice/42")


def test_unknown_provider_delivery_url_rejected_as_catalogue():
    config = Config(options={
        ("synthetic-provider", "catalogue_url", "alice"):
            "https://cdn.synthetic.example/asset.acsm?token=private",
    })
    host = _host("synthetic-provider", config=config)
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "synthetic-provider", "source_credential": "alice"},
    ) is None


def test_catalogue_option_never_special_cases_kobo_in_core():
    import ast
    from pathlib import Path
    source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = next(
        item for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "LibraryService"
        for item in node.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "public_catalogue_target"
    )
    source_method = ast.get_source_segment(source, method)
    assert "elif provider == 'kobo':" not in source_method
    assert "provider, 'catalogue_url', None" in source_method
    assert "if provider == 'paris':" in source_method


def test_minimal_catalogue_host_without_provider_option_still_uses_homepage():
    host = _host(
        "synthetic-provider", homepage="https://public.synthetic.example/",
        config=NS(get=lambda _key, default: default),
    )
    assert LibraryService.public_catalogue_target(
        host, {"provider_id": "synthetic-provider"},
    ) == ("catalogue", "https://public.synthetic.example/")


def test_minimal_catalogue_host_without_config_keeps_attested_absolute_notice():
    host = NS(
        provider_descriptors=lambda: [],
    )
    assert LibraryService.public_catalogue_target(
        host, {
            "provider_id": "synthetic-provider",
            "friendly_url": "https://public.synthetic.example/book/42",
        },
    ) == ("notice", "https://public.synthetic.example/book/42")
