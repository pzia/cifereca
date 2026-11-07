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


# Versioned public guide mirrored from docs/PROVIDER-AUTONOMOUS-DEVELOPMENT.md.
# Kept self-contained because the Browser Recorder ZIP must work offline in
# installed wheels, without access to the repository documentation tree.
_PROVIDER_GUIDE = "# Guide autonome — développer un provider Cifereca depuis une capture Browser Recorder\n\nVersion documentaire : 2026-11-07. **Ce guide explique les règles ; les signatures exactes du Core exécutant l'export sont dans `plugin-dev-kit/contracts/` et `contract-registry.json`.** Les contrats normatifs du dépôt restent `docs/ARCHITECTURE.md`, `docs/PLUGIN-BOUNDARIES.md` et `cifereca.plugin_api`. Une capture ne prouve pas à elle seule la faisabilité d'un prêt ou d'un téléchargement.\n\n## 1. Décider ce qu'est le provider\n\n- `provider.library` : compte/credential/identité, inventaire personnel des achats ou prêts, recherche si prise en charge, opérations fournisseurs qualifiées. **Une session distincte par credential et identité.**\n- `catalog.service` : service technique de recherche/normalisation de notices publiques, sans compte possédé, droits, prêts, placements ni téléchargement implicite.\n- `provider.collection` : inventaire virtuel selon son contrat ; ne pas le confondre avec le catalogue public.\n- Une **Notice C** décrit un ouvrage ; elle ne prouve ni achat, ni prêt, ni droit, ni octets locaux. Ne jamais déduire une acquisition d'un résultat de recherche, d'un ISBN, d'un titre, d'une couverture, d'une URL de fiche ou d'une disponibilité commerciale.\n- Séparer explicitement **catalogue public**, **inventaire personnel authentifié**, **état de prêt/droit**, **offres de livraison**, **réception effective**, **artefact et placement**. Les opérations de consultation n'effectuent ni GET de livraison, ni prêt, ni retour, ni renouvellement.\n- Recherche : publier uniquement les champs réellement observés et leur provenance ; pagination, curseurs, filtres, tri, langue, formats, couverture, fraîcheur et caractère partiel doivent être explicités. `HTTP 200` n'est pas une recherche complète ; un résultat vide partiel n'est pas une preuve d'absence. Respecter `ProviderSearchCapabilities`, `SearchResult`, `ItemDetail` et les modèles effectivement exposés par l'API installée.\n\n## 2. Isolation Core / plugin : frontière non négociable\n\n- **Core** : orchestration A→B, sélection d'identité/compte, consentement et révocation, verrouillage et annulation, transport générique via broker, cache transversal/TTL/single-flight, tâches, stockage/SQLite, SHA-256, Artifact, Placement, OriginLink, journal, publication atomique, récupération après crash, gestion des droits d'accès aux profils.\n- **Plugin** : protocoles HTTP/HTML/OPDS, parcours SSO/MFA, mapping de champs, identifiants d'édition/branche, observations fournisseurs et leur couverture, offres/capacités, erreurs fournisseurs, éventuels quotas observés. Les transformations DRM utilisent les runtimes et contrats dédiés ; le plugin n'effectue pas de déchiffrement Core caché.\n- Imports de production : `from cifereca.plugin_api import ...` et modules internes **du même package plugin**. **Interdit** : `cifereca.storage`, `cifereca.services`, modules `cifereca.core.*` ou GUI privée, SQLite Core, chemins internes, introspection des sessions d'un autre provider. Si un contrat manque, ouvrir un ticket Core et fournir un test de contrat ; ne pas contourner.\n- Le Core ne doit connaître ni le nom du site, ni ses routes, ni ses quotas ; il manipule des identifiants opaques et des preuves typées. Ne pas mutualiser un comportement seulement parce que deux sites semblent similaires : vérifier l'équivalence.\n- Utiliser les brokers publics disponibles dans `context` et les `requires_brokers` déclarés ; pas de client HTTP global hors contrôle, ni d'import dynamique du code privé Core.\n\n## 3. Identité, provenance, isolation des sessions et caches\n\n- Épingler chaque action à `(identity_id, provider_id, credential_id, account_id, branch, item_or_edition, session_epoch)` selon les champs disponibles ; ne jamais substituer le profil UI actuellement affiché à l'identité sélectionnée lors d'une tâche asynchrone.\n- Cloisonner cookies, jetons, SSO, credentials, état de connexion, cache authentifié, pagination, quota, et verrous par compte/tenant/identité. Pas de session HTTP partagée entre établissements ou comptes, même si domaine et moteur sont identiques.\n- Cache public : clé tenant/endpoint/requête/locale/version de parseur, TTL et provenance. Cache authentifié : ajouter identité, provider, credential, compte, branche et génération de session. Une erreur/timeout/403 ne doit jamais être remplacé par un ancien succès présenté comme frais.\n- Revalider session vivante, identité, credential, compte, consentement et source avant toute opération sensible et avant le commit Core. Rotation ou déconnexion invalide les tâches et projections associées.\n- Un même ISBN, titre, SHA, chemin ou fichier sous un autre profil n'établit **aucun** droit. Deux profils peuvent partager l'Artifact global par SHA mais jamais le Placement physique ni l'OriginLink de droit.\n- Les identifiants techniques d'établissement, de prêt, de licence, de livre et d'édition sont des dimensions distinctes : ne pas les confondre ni les reconstruire à partir de titres.\n\n## 4. Prêts, achats, offres, DRM : sémantique d'autorité\n\n- Un inventaire de **prêts actifs** lu et interprété avec succès pour le bon compte/branche, y compris vide, et avec pagination requise complète, peut constater la fin d'un prêt précédemment actif **de ce scope**. Lecture partielle, cache, changement de compte, erreur, annulation ou pagination inachevée : aucune clôture par absence.\n- Un achat absent du catalogue personnel ne révoque **jamais** automatiquement le droit historique, les octets ni leur provenance. Les acquisitions et prêts ont des règles d'absence différentes.\n- Distinguer : droit descriptif `unknown`, refus explicite, offre de livraison positivement attestée, et téléchargement effectivement reçu. Un droit descriptif facultatif inconnu n'interdit pas une capacité de livraison indépendante prouvée ; une offre inconnue n'est pas une autorisation. Respecter les refus explicites.\n- Aucun prêt/retour/renouvellement/GET d'ACSM ou d'EPUB par défaut : action et consentement Core explicites, garde contre double POST/GET, idempotence/reprise et preuves de réception. Un ACSM, un HTTP 200 ou une ligne legacy `complete` ne prouve pas un EPUB présent.\n- Le Core publie après traitement effectif : SourceReference exacte → preuve de livraison → octets finaux vérifiés → SHA → Artifact/Placement/OriginLink. Les artefacts DRM/ADEPT/LCP et licences ne doivent pas être interprétés comme des fichiers lisibles sans validation du runtime correspondant.\n- Les quotas, durées et limites sont des **observations du compte ou de l'établissement**, avec source/date/couverture. Une limite annoncée n'est ni une permission d'emprunter ni une disponibilité instantanée.\n\n## 5. Bundles multi-établissements (exemple BiblioAccess)\n\nUn package `provider-biblioaccess` peut déclarer plusieurs entrées `plugins[]` de type `provider.library` dans **un seul** `cifereca-plugin.json`. Chaque entrypoint est un wrapper mince autour du moteur du même package. Les providers `provider.biblioaccess.place-des-arts` et `provider.biblioaccess.la-rochelle` ont chacun leur schéma de configuration, credential, SSO, tenant et session. `1717` est attesté pour Place des Arts ; l'identifiant La Rochelle doit être relevé, pas deviné. Ne pas assimiler BiblioAccess prêt à Numilog achats.\n\nLes manifests supportent `requires`, `requires_contracts`, `requires_brokers`, `config_schema` et les versions : ne déclarer une capacité qu'après preuve, implémentation et tests. Ne pas créer un provider générique supplémentaire visible à l'utilisateur uniquement pour partager du code. Si le moteur est dans un package distinct, utiliser une dépendance et un contrat technique versionné plutôt qu'un import direct non déclaré.\n\n## 6. Exploiter une capture sans inventer d'API\n\n1. Lire d'abord `plugin-dev-kit/README.md`, `LLM_GUIDE.md`, `contract-registry.json` et `contracts/` ; les versions embarquées font foi. Ce guide n'autorise pas l'utilisation d'API absentes du kit.\n2. Identifier origine, tenant, redirections SSO, pages publiques, réponses XHR, pagination, fiches, liste de prêts, état de connexion et offres. Séparer les faits observés des hypothèses ; relever les corps absents, masqués, compressés ou hors origine.\n3. **ZIP revu** : projections filtrées/anonymisées, potentiellement insuffisantes pour reconstruire les champs ; **ZIP privé** : données personnelles en clair, sans cookies/Authorization collectés, à garder confidentiel. Aucun des deux n'est un HAR complet, ni une preuve de credentials utilisables.\n4. Produire une matrice endpoint/méthode/authentification/tenant/paramètres/structure/erreurs/pagination/couverture/état, avec statut `observé`, `supposé` ou `non capturé`. Ne jamais rejouer un POST de prêt ou une URL de téléchargement sur la seule base d'une trace.\n5. Écrire fixtures anonymisées et doubles HTTP offline. Qualifier explicitement la compatibilité de deux établissements sur la même séquence, pas seulement leur domaine commun.\n\n## 7. Livraison autonome et recette minimale\n\n- Package : `src/plugins/provider-<nom>/cifereca-plugin.json`, modules d'entrypoint, README d'installation/configuration, tests unitaires, fixtures et tests de contrat public. S'appuyer sur `src/plugins/provider-reference/`, `docs/plugin-examples/provider/` et `scripts/plugin_packaging.py`.\n- Tests : plusieurs identités et credentials ; deux tenants sur même domaine ; cookie/SSO/cache isolés ; connexion révoquée ; 403, timeout, redirect externe, CSRF, réponse tronquée/compressée ; pagination vide/partielle ; identifiants d'édition divergents ; même SHA entre profils ; prêt terminé seulement après inventaire complet ; offre inconnue/refus ; crash/replay sans second GET/POST ; annulation ; aucun accès Storage privé.\n- Tests catalogue : résultat vide authentique vs incomplet, notices sans droit, filtres/tri/pagination/locale, images et provenance, quotas observés vs autorisations, aucune action mutante lors de recherche/sync catalogue.\n- Exécuter tests plugin, tests de contrat Core/plugin, packaging/install/rollback et CI sur HEAD exact ; conserver une matrice de conformité et les limites non qualifiées. Ne pas prétendre qu'une capture valide un prêt réel, une licence DRM ou une acquisition.\n- **Ne jamais embarquer dans le ZIP de capture** : secrets, cookies, clés, données Core privées, captures non revues en prétendant qu'elles sont anonymisées, ou une implémentation prétendument exécutable. Le kit est un guide et des contrats publics, pas un accès à un compte.\n\n## Références de travail (dépôt)\n\n`docs/ARCHITECTURE.md`, `docs/PLUGIN-BOUNDARIES.md`, `docs/CORE-P1-2385-CATALOGUE-INVENTORY-CONTRACT.md`, `docs/PLUGIN-SEARCH-CAPABILITIES-1917.md`, `docs/CORE-P1-3119-LOAN-INVENTORY-CONTRACT.md`, `docs/PLUGINS-P0-3628-LOAN-PRODUCERS-TYPED-MIGRATION.md`, `src/plugins/browser-recorder/README.md`. Tickets : #3171, #3196, #2385, #3252, #2056, #3691. Les documents historiques décrivent parfois un état partiel : vérifier la version du Core exécutant la capture.\n"


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
        "plugin-dev-kit/PROVIDER-AUTONOMOUS-DEVELOPMENT.md": _PROVIDER_GUIDE.encode("utf-8"),
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
    provenance["plugin-dev-kit/PROVIDER-AUTONOMOUS-DEVELOPMENT.md"] = (
        "docs/PROVIDER-AUTONOMOUS-DEVELOPMENT.md"
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
