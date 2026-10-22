"""Deterministic offline Core contract kit for reviewed Browser Recorder ZIPs.

Contract sources are public `cifereca.plugin_api` modules from the *running*
Core, never private application modules or data from the recorded browser.
Zero network I/O, zero user-controlled filenames and no Git introspection.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata, resources
import json

from browser_recorder import RecorderError
from browser_recorder_devkit_templates import reference_plugin_files

_KIT_SCHEMA = "cifereca.browser-recorder.plugin-dev-kit/1"
_CORE_API_PACKAGE = "cifereca.plugin_api"
# Only the published API boundary; no application DB, plugin credentials,
# private Core services, release files, local logs or user data.
_PUBLIC_SOURCES = (
    "__init__.py",
    "version.py",
    "contracts.py",
    "models.py",
    "context.py",
    "brokers.py",
    "provider_collection.py",
    "provider_search_stream.py",
    "errors.py",
)
_MAX_SOURCE_BYTES = 160_000
_MAX_KIT_BYTES = 550_000

_GUIDE = """# Cifereca: kit public de création de plugin (hors connexion)

Ce dossier contient la copie **exacte et datée par version** des sources
publiques `cifereca.plugin_api` lues depuis le Core exécutant l'export,
ainsi que `contract-registry.json` et un manifeste SHA-256. Le contenu des
fichiers `contracts/*.py` est **normatif** pour la version constatée ;
le présent guide est explicatif, pas une API alternative.

## Construire un plugin sans inventer de contrat

1. Examiner `contract-registry.json`, puis
   `contracts/__init__.py`, `contracts/models.py`,
   `contracts/brokers.py`, `contracts/context.py`.
   Le registre distingue `provider.library` (identité/bibliothèque),
   `catalog.service` (catalogue technique), `provider.collection`
   (inventaire virtuel), `ui.page` et les autres catégories.
2. Créer un dossier `src/plugins/provider-nom/` avec
   `cifereca-plugin.json`, un module Python d'entrypoint `module:PLUGIN`,
   `tests/test_nom.py` et un README. Importer les contrats publics avec
   `from cifereca.plugin_api import ...` uniquement. Ne pas importer
   `cifereca.services`, `cifereca.storage`, ni les classes internes Core.
3. Exemple **illustratif**, à compléter avec les signatures du contrat choisi
   (un manifeste seul n'est pas un provider fonctionnel) :

```json
{
  "manifest": 1,
  "package": {
    "id": "org.cifereca.provider.exemple",
    "version": "0.1.0",
    "name": "Exemple de provider",
    "publisher": "local",
    "plugin_api": ">=1.4,<2"
  },
  "plugins": [
    {
      "id": "provider.exemple",
      "type": "provider.library",
      "version": "0.1.0",
      "entrypoint": "exemple:PLUGIN",
      "display_name": "Exemple",
      "enabled_by_default": false,
      "capabilities": [],
      "requires": [],
      "recommends": [],
      "system_requires": [],
      "requires_brokers": [],
      "requires_contracts": []
    }
  ]
}
```

Les capacités illustrées sont **vides par défaut** : ne les affirmer qu'après
preuve de la source, implémentation et tests. Pour un catalogue purement public
séparé du compte, étudier plutôt `catalog.service`. Pour un prêt numérique
ou ACSM, l'autorisation et la capacité effective doivent être établies
séparément ; aucun `POST` de création de prêt par défaut.

## Exemple de plugin réellement testable

Le dossier `examples/provider-fixture-example/` du même ZIP contient un
**projet complet et autonome côté plugin** :
`cifereca-plugin.json`, `fixture_catalog.py`, `tests/` et README.
Il fournit la catégorie publique `catalog.service/1`, trois notices
entièrement inventées, recherche/pagination, détail, erreurs typées,
cache limité, et **aucune acquisition, propriété ni emprunt présumés**.
Copier le dossier dans le dépôt cible puis lancer les tests et l'outil
de packaging officiel de cette version de Core. Il ne contacte aucun site.
L'exemple n'est pas la reconstitution d'un fournisseur réel, mais un
référentiel de conformité auquel confronter les choix d'un LLM.

## Adapter la trace anonymisée

- `../timeline.json` : ordre, méthodes, onglets/pages, codes, durées et redirections retenues.
- `../coverage-report.json` : états de capture et de projection par requête/réponse,
  ainsi que les omissions. Contrôler ce rapport avant toute conclusion négative.
- `../exchanges/*.json` : méthodes, structures, chemins transformés.
- `../bodies/*.txt` : corps conservés **après** projection.
- `../manifest.json` : omissions et limites de couverture.
- `../identifiers.json` : décisions individuelles de revue.

Établir une matrice **observé / inféré / inconnu**, par endpoint et droit :
recherche, pagination, notice, compte, prêt existant, téléchargement,
renouvellement, authentification, erreurs, formats, acquisition, DRM.
Une requête `GET` de retéléchargement ACSM n'implique PAS qu'un emprunt ait
été créé. Un HTTP 200 ne démontre ni possession, ni capacité d'emprunt.
Les routes `~route-NNNN~` sont des étiquettes **opaques locales au ZIP**,
pas les vrais endpoints. Un corps omis ne décrit pas un schéma complet.

## Implémentation / vérification

- Concevoir transport avec TLS, timeouts, pagination et limites, anti-SSRF,
  cache par identité et politiques de rétention ; éviter les copies de tokens.
- Utiliser `PluginContext.broker(...)` pour les dépendances disponibles :
  tâches/annulation, cache, transport, acquisition, diagnostics ; vérifier
  les Protocols correspondants dans les sources, ne pas deviner d'API privée.
- N'émettre que des diagnostics expurgés, avec codes fermés ; jamais de
  contenu HTTP privé, compte, livre ou ACSM dans logs et tests publics.
- Tester recherche/détail, erreurs 401/403/429, couverture des champs,
  pagination, auth consentie, annulation et non-rejeu des actions sensibles.
- Valider le manifeste via l'outil officiel de packaging du dépôt cible et
  les tests Core/publics ; ne pas livrer un navigateur ni des secrets.
  Sans dépôt Core, noter explicitement que packaging/installabilité sont
  **non vérifiés** et fournir des fixtures indépendantes.

Ne pas inclure ni restituer de cookies, mots de passe, tokens, identifiants
privés, DRM, URL d'activation ou données personnelles dans le code proposé.
Un ZIP revu et filtré reste une source **partielle et heuristique** ; les
identifiants expressément conservés peuvent être privés, malgré la revue.
"""

_LLM_GUIDE = """# Instructions à un LLM de génération de plugin

Entrées: `manifest.json`, `coverage-report.json`, `timeline.json`,
`exchanges/`, `bodies/`, `identifiers.json`, puis **ce kit** `plugin-dev-kit/`.

1. Vérifier `manifest.json` de ce kit, ses hash SHA-256, la version
   `plugin_api_version` et la compatibilité avec le Core cible.
2. Lire les véritables contrats de `contracts/*.py` ; ne pas inventer
   les méthodes ni imports. Vérifier `contract-registry.json`.
3. Produire un tableau endpoint, méthode, statut, corps observés,
   hypothèses, manques, origine de preuve, droits et risques.
4. Distinguer catalogue public, bibliothèque authentifiée, prêt existant,
   création d'emprunt **non prouvée**, acquisition ACSM et téléchargement.
5. Construire transports et DTO typés, pagination/caches, plugins Python,
   manifeste, tests pytest locaux 100 % fictifs, et notices d'installation.
6. Expliquer les limites du ZIP : URLs transformées, champs exclus,
   corps omis et absence d'authentification rejouable. Contrôler les états
   `capture_state` et `export_state` par échange avant d'inférer un schéma.
   La revue ne garantit pas l'absence de données personnelles.

Livrables: API/permissions constatées, incertitudes clairement marquées,
arborescence de plugin, code importable, tests, commandes de validation,
hypothèses de compatibilité et aucune action non autorisée.
N'exécuter aucune requête vers un fournisseur réel sans nouvelle autorisation.
"""


def core_contract_kit() -> dict[str, bytes]:
    """Return immutable source/docs ZIP members; never use input capture data."""
    try:
        from cifereca.plugin_api import (
            CONTRACT_REGISTRY, MANIFEST_VERSION, PLUGIN_API_VERSION,
        )
        package = resources.files(_CORE_API_PACKAGE)
        core_version = metadata.version("cifereca")
    except metadata.PackageNotFoundError:
        core_version = "unknown"
        try:
            from cifereca.plugin_api import (
                CONTRACT_REGISTRY, MANIFEST_VERSION, PLUGIN_API_VERSION,
            )
            package = resources.files(_CORE_API_PACKAGE)
        except Exception as exc:
            raise RecorderError("core_kit_unavailable") from exc
    except Exception as exc:
        raise RecorderError("core_kit_unavailable") from exc

    if (type(PLUGIN_API_VERSION) is not str
            or type(MANIFEST_VERSION) is not int
            or not isinstance(CONTRACT_REGISTRY, tuple)):
        raise RecorderError("core_kit_incompatible")

    contents = {
        "plugin-dev-kit/README.md": _GUIDE.encode("utf-8"),
        "plugin-dev-kit/LLM_GUIDE.md": _LLM_GUIDE.encode("utf-8"),
    }
    provenance = {}
    total = sum(len(v) for v in contents.values())
    for name in _PUBLIC_SOURCES:
        if not name.endswith(".py") or "/" in name or ".." in name:
            raise RecorderError("core_kit_invalid")
        try:
            data = package.joinpath(name).read_bytes()
        except Exception as exc:
            raise RecorderError("core_kit_unavailable") from exc
        if (not data or len(data) > _MAX_SOURCE_BYTES
                or bytes((0,)) in data):
            raise RecorderError("core_kit_invalid")
        total += len(data)
        if total > _MAX_KIT_BYTES:
            raise RecorderError("core_kit_limit")
        target = "plugin-dev-kit/contracts/" + name
        contents[target] = data
        provenance[target] = "src/cifereca/plugin_api/" + name

    rows = [asdict(row) for row in CONTRACT_REGISTRY]
    if len(rows) > 100 or any(
        type(row.get("category")) is not str for row in rows
    ):
        raise RecorderError("core_kit_incompatible")
    contents["plugin-dev-kit/contract-registry.json"] = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, indent=2,
    ).encode("utf-8")
    provenance["plugin-dev-kit/contract-registry.json"] = (
        "cifereca.plugin_api.CONTRACT_REGISTRY"
    )
    # Reference package is fixture-only. Include its tests and manifest
    # inside the exact reviewed ZIP, not as code run during capture/export.
    examples = reference_plugin_files()
    if not examples or any(
        not name.startswith("plugin-dev-kit/examples/provider-fixture-example/")
        or name in contents or not isinstance(data, bytes)
        for name, data in examples.items()
    ):
        raise RecorderError("core_kit_invalid")
    contents.update(examples)
    for name in examples:
        provenance[name] = (
            "src/plugins/browser-recorder/"
            "browser_recorder_devkit_templates.py:reference_plugin_files"
        )
    provenance["plugin-dev-kit/README.md"] = (
        "src/plugins/browser-recorder/browser_recorder_devkit.py:_GUIDE"
    )
    provenance["plugin-dev-kit/LLM_GUIDE.md"] = (
        "src/plugins/browser-recorder/browser_recorder_devkit.py:_LLM_GUIDE"
    )
    manifest = {
        "schema": _KIT_SCHEMA,
        "core_version": core_version or "unknown",
        "plugin_api_version": PLUGIN_API_VERSION,
        "manifest_version": MANIFEST_VERSION,
        "git_revision": "unknown",
        "generation": "offline, running Core public API",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "documentation_is_complete_for": [
            "plugin_api public source boundary",
            "contract registry and manifest version",
            "catalog.service/1 offline reference example and tests",
        ],
        "not_claimed": [
            "private Core implementation",
            "provider-specific authentication/permissions",
            "full HTTP capture or authenticated replay",
            "guaranteed installability on a different Core version",
        ],
        "files": [
            {
                "path": name,
                "sha256": sha256(data).hexdigest(),
                "bytes": len(data),
                "source": provenance[name],
            }
            for name, data in sorted(contents.items())
        ],
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2,
    ).encode("utf-8")
    if sum(len(value) for value in contents.values()) + len(manifest_bytes) > _MAX_KIT_BYTES:
        raise RecorderError("core_kit_limit")
    contents["plugin-dev-kit/manifest.json"] = manifest_bytes
    return contents
