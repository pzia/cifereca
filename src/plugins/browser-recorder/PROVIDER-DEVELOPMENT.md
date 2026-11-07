# Développer un provider Cifereca — contrat d'autonomie et d'isolation

> Guide de référence, 07/11/2026. Source canonique dans `docs/PROVIDER-DEVELOPMENT.md`, copie **strictement identique** livrée avec Browser Recorder. Le ZIP d'une capture inclut cette copie sous `plugin-dev-kit/PROVIDER-DEVELOPMENT.md`. Les signatures de `cifereca.plugin_api` du Core réellement installé, exportées sous `plugin-dev-kit/contracts/`, **prévalent** sur les exemples et toute ancienne note. Contrats directeurs : [ARCHITECTURE](ARCHITECTURE.md), [PLUGIN-BOUNDARIES](PLUGIN-BOUNDARIES.md), #3171, #3719, #2385 et #4552.

## 1. Frontière impérative : plugin = faits, Core = décisions

| Responsabilité | Plugin provider | Core Cifereca |
| --- | --- | --- |
| Authentification/SSO | Formulaires, redirections, CSRF, cookies et renouvellement de session propres à la source, sous consentement et contraintes d'accès | Identité Cifereca, liaison de compte, portée du credential, révocation du contexte |
| Catalogue / notice C | Recherche publique/privée, pagination, filtres **réellement disponibles**, détails et valeurs sourcées | Cache/projection génériques, consolidation persistante des notices et provenances par champ (chantier #4552), vues Recherche |
| Inventaire personnel A | Prêts/achats/ressources positivement observés avec source, compte, branche, édition, révision et **couverture de lecture** | `SourceReference`, qualification des preuves, politiques d'absence, convergence |
| Livraison B | Offre de fichier et capacité de téléchargement attestées, octets/ACSM/LCPL via protocole autorisé, staging confié par Core | Consentement ON/OFF par source, verrou/epoch, admission, SHA, Artifact, OriginLink, Placement, transaction et reprise |
| Erreur/quota | Codes fournisseur, état de session, quota effectivement observé, échéances, 401/403/429 | Sémantique de résultat, annulation, UI, métriques/Diagnostics, protection interprofils |
| Fichiers et DRM | Déclarer le type de contenu et la méthode effectivement supportée ; déléguer aux runtimes publics | Chemins contrôlés, ADEPT/LCP supportés explicitement, publication/reçus ; pas de conversion de droits supposée |

**Interdits :** import de `cifereca.services`, `cifereca.storage`, `cifereca.core.*`, `cifereca.gui.*`, SQL interne, chemins SQLite ou `backup_items` ; accès à un profil choisi dans l'UI comme substitut du scope demandé ; écriture directe d'un `Artifact`, `OriginLink`, `Placement`, entitlement ou reçu final. Ne pas introduire dans le Core un `if provider == "biblioaccess"/"kobo"/...` : un contrat manquant doit être ajouté **publiquement, versionné, optionnel et testé**. Les clients passent par `PluginContext.broker(...)`, `service(...)`, `services(...)` et les Protocols publiés ; ne pas deviner une méthode de broker.

## 2. Choisir la catégorie et le package

- `provider.library` : compte/identité, catalogue éventuellement privé, inventaire d'achats ou de prêts, offres d'acquisition sous contrats publics ; instancier une `ProviderSession` par credential et isoler ses comptes.
- `catalog.service` : catalogue public/technique seulement. Aucun droit de compte, prêt ni livre possédé inféré de ses notices. Peut être fourni par le même bundle si besoin.
- `catalog.enrichment` : catégorie déclarée mais **pas d'API opérationnelle universelle à présumer** ; vérifier l'état du contrat #4552 avant de coder un consolidateur.
- `provider.collection` : découverte versionnée de fournisseurs virtuels **si** le besoin de découverte dynamique est démontré ; ce n'est pas une obligation pour deux providers statiques. `ui.page`, `drm.handler`, `runtime.adapter`, `storage.target` sont des catégories distinctes, jamais des permissions implicites.
- **Bundle multi-établissements** : un package `provider-biblioaccess` avec moteur partagé et plusieurs descripteurs `plugins[]` de type `provider.library`. Ex. `provider.biblioaccess.place-des-arts` (tenant confirmé 1717) et `provider.biblioaccess.la-rochelle` (tenant inconnu tant que non capturé). Chaque entrypoint crée une façade session/configuration spécifique, **pas un clone du moteur**. Un `requires` entre plugins est un lien déclaré, pas une licence d'importer les modules d'un autre ZIP : préférer le même package ou un contrat public dédié.
- Les **identifiants du plugin, package, tenant, credential et compte** sont distincts. Un même domaine HTTPS, moteur ou opérateur commercial n'implique jamais une session partagée. Le provider achats `provider.numilog` ne partage ni cookies ni droits avec BiblioAccess prêt.

Exemple **schématique** de manifeste (adapter versions, entrypoints, brokers, capacités et `config_schema` à l'API vérifiée ; aucune capacité acquise par simple déclaration) :

```json
{
  "manifest": 1,
  "package": {
    "id": "org.cifereca.provider.biblioaccess",
    "version": "0.1.0",
    "name": "BiblioAccess",
    "publisher": "cifereca",
    "plugin_api": ">=1.4,<2"
  },
  "plugins": [
    {
      "id": "provider.biblioaccess.place-des-arts",
      "type": "provider.library",
      "version": "0.1.0",
      "entrypoint": "place_des_arts:PLUGIN",
      "display_name": "Place des Arts — Prêt numérique",
      "enabled_by_default": false,
      "capabilities": [],
      "requires": [], "recommends": [],
      "requires_brokers": ["httpx"], "requires_contracts": []
    },
    {
      "id": "provider.biblioaccess.la-rochelle",
      "type": "provider.library",
      "version": "0.1.0",
      "entrypoint": "la_rochelle:PLUGIN",
      "display_name": "La Rochelle — Prêt numérique",
      "enabled_by_default": false,
      "capabilities": [],
      "requires": [], "recommends": [],
      "requires_brokers": ["httpx"], "requires_contracts": []
    }
  ]
}
```

Il s'agit d'une **cible illustrative non installée**, pas d'un provider ayant ses capacités prouvées. La catégorie `provider.library` est `cifereca.provider/1`. Le manifeste est validé par `src/cifereca/plugins/manifest.py` et `scripts/plugin_packaging.py` ; plusieurs providers dans un seul package sont pris en charge. Les capacités `catalog.search`, `catalog.detail`, `sync.loans`, `acquisition.borrow` ou `acquisition.download` ne doivent être ajoutées que lorsque le code et ses tests les réalisent, distinctement des permissions d'un compte.

### Champs de configuration publics (contrat réel)

La classe `ProviderDescriptor` du Core expose notamment `credential_fields: tuple[CredentialField, ...]`, `account_options: tuple[AccountOptionField, ...]`, `borrower_scoped_options: tuple[BorrowerScopedOptionField, ...]`, `interactive_authentication: bool` et `max_accounts_per_identity`. Le kit de capture inclut leur définition exacte dans `plugin-dev-kit/contracts/provider_base.py`, car ces classes, bien que définies dans `cifereca.providers.base`, sont **réexportées publiquement** par `cifereca.plugin_api`.

- `CredentialField(key, label, secret, required, placeholder)` : déclarer les identifiants/secrets nécessaires à l'établissement ; le schéma d'authentification varie par provider et ne doit pas être partagé avec une autre source.
- `AccountOptionField(key, label, default, required, secret, legacy_config_key, value_type, min_value, max_value, help_text)` : options de compte, par exemple une préférence ou un entier de politique; **pas** une autorité sur un quota réellement disponible.
- `BorrowerScopedOptionField` : paramètres déclaratifs par emprunteur/branche seulement si un établissement expose plusieurs comptes liés ; ne pas inventer de sous-comptes.
- `ProviderPlugin.create_session(*, credential_id, label, credentials, options, config, storage, diagnostics)` : signature historique exacte actuellement réexportée. **Le paramètre hérité `storage` n'autorise aucune écriture directe dans le Core** ; ne pas exploiter des détails internes de l'objet. `ProviderSession.connect()` renvoie les comptes upstream et `close()` invalide l'état de session ; vérifier les signatures et capacités du code fourni avec le kit, non de snippets anciens.

Chaque façade d'établissement fixe son propre `ProviderDescriptor.id`, son `tenant_id` vérifié et ses champs de credentials. Pour un login interactif/SSO, `interactive_authentication=True` signale une connexion qui **ne doit pas être lancée automatiquement** par le balayage de démarrage. Les quotas et échéances effectifs restent des observations vivantes du compte, jamais un simple paramètre qui autorise `borrow()`.

## 3. Catalogue C, inventaire A, fichier B : ne jamais fusionner

| Surface | Peut attester | Ne peut pas attester |
| --- | --- | --- |
| Recherche C publique | Titre, auteur, édition/format, identifiant bibliographique, disponibilité annoncée, notice, couverture | Achat, prêt, droit, possession, fichier local, téléchargeabilité pour ce compte |
| Recherche C authentifiée | Les mêmes notices et un **état de consultation affiché** du compte si réellement observé | Un emprunt créé, un livre téléchargé ou un inventaire exhaustif |
| Inventaire A du compte | Ressources exactes connues, emprunteur, branche achat/prêt, identifiant d'édition, offre de téléchargement actuelle, limites/couverture | Présence de bytes dans un profil, preuve d'un second compte |
| Livraison B vérifiée par Core | Artifact final, SHA, origine du fournisseur, Placement exact physiquement présent, reçu achevé | Droit implicite dans d'autres profils ou disponibilité perpétuelle |

**Clés :** toujours publier les coordonnées exactes `identity_id / provider_id / credential_id / account_id / branch / item-or-edition / resource / kind / revision` lorsque le contrat les demande. Distinguer ISBN papier/numérique, titre, `record_id`, identifiant de ressource de livraison et identifiant de prêt : ils ne sont pas interchangeables. Conserver la provenance des champs ; plusieurs catalogues peuvent décrire une même œuvre, sans fusionner les acquisitions. Ne pas coder un consolidateur métier dans le provider : fournir des observations documentaires sourcées, le Core #4552 choisit la consolidation. Ce dernier chantier est **en cours** : une intention d'architecture n'est pas une API disponible.

**Catalogue synchronisé ≠ bibliothèque personnelle :** `catalog.search/detail` décrit une recherche ponctuelle ; un cache local ou un OPDS ne prouve pas `catalog.refresh` ou `catalog.snapshot`. La projection Core `CatalogueSourceCapability` (#2385) distingue déjà `public/authenticated`, recherche disponible, inventaire personnel et source activée ; `catalog_refresh` reste non pris en charge tant qu'un contrat versionné et un producteur ne l'attestent pas. Le cache public et les listes privées ont des clés, TTL, autorisations et invalidations séparés. À la lecture d'une notice, **zéro POST de prêt et zéro téléchargement implicite**.

**Pagination et couverture :** la source annonce `complete / partial / failed` au niveau **compte + branche + requête**, sans transformer HTTP 200 en complétude. Une limite de pages, un postfiltre local, un doublon entre pages, une erreur 429 ou un curseur absent doivent être visibles. Une pagination interrompue ne signifie pas « zéro livre » ; les échecs doivent préserver les derniers faits positifs valides, sans simuler une liste négative. Prêts : seule une lecture **réellement réussie et interprétée** de la liste courante du prêteur exact, avec pagination requise achevée, peut terminer par absence un ancien prêt. Achats : une absence d'inventaire ne révoque pas droit, fichier ou provenance, même après lecture réussie (#3171).

## 4. Contrats d'acquisition, emprunt et DRM

- **Ressources téléchargeables :** `cifereca.downloadable-resource/2` dans l'API publique ; `downloadable_resource_snapshot`, `downloadable_selected_offer_snapshot`, `downloadable_selected_delivery_candidate` et variantes runtime/guarded disponibles **seulement si leurs vraies signatures sont respectées**. Le Core qualifie les offres, le scope, le consentement par source et l'état de session avant la livraison ; le plugin ne crée pas de reçu B final.
- **Droit descriptif facultatif :** une capacité de téléchargement positive, autorisée et spécifique au compte peut être exploitable avec `right=unknown` ou absent ; l'inverse est faux : `right=active` sans offre de fichier ne permet pas de GET. Un refus explicite du fournisseur reste opposable. Ne jamais inférer une route de téléchargement à partir d'un ISBN, titre, SHA ou lien d'une autre identité.
- **Prêts :** le support statique d'une action et la connexion active ne sont ni disponibilité d'une offre ni permission d'emprunter. Le quota réellement annoncé par le compte, les dates d'échéance, les limitations de branche et l'état de prêt sont des observations distinctes. Création d'un prêt : **action explicite et permission actuelle**, jamais à la recherche, dans `connect()`, à l'ouverture de la fiche ou lors de Sync. Ne pas définir une opération de restitution automatique hors contrat produit.
- **ACSM/Adobe** et **LCPL/Readium LCP** sont deux parcours de licence différents ; aucun renommage ni déchiffrement supposé. Le plugin décrit `format`, `media_type`, `drm`, les candidats et preuves ; le runtime public approprié traite ce qui est autorisé. Livres audio/streaming : pas d'Artifact EPUB fictif.
- **Relance :** un reçu interrompu ou un ancien chemin ne vaut pas autorisation d'un second GET. Garder la cohérence du compte et des révisions pendant les callbacks/SSO et jusqu'au commit, signaler une tentative ambiguë, permettre la reprise supervisée. Le Core décide idempotence/dédup, écrit les reçus et contrôle SHA/FS/SQLite.

## 5. Authentification, sécurité, isolation et cache

Le scope minimal d'une session privée et de ses caches est **`provider_id + tenant_id + identity_id + credential_id + account_id`**, complété par branche, édition/révision et génération de session selon l'objet. Les caches **publics** peuvent être mutualisés uniquement si leur contenu, provenance et conditions d'accès sont réellement publics ; jamais réutiliser un cookie ou un cache de réponse privée pour un autre établissement. Un changement d'identité, rebind de credential, login/logout, tenant, epoch, connexion désactivée ou consentement révoqué doit rendre les travaux asynchrones et leurs résultats anciens inertes.

- `base_url` et `tenant_id` sont des configurations **contrôlées** ; valider schéma, HTTPS, host, redirections, `Location`, hôtes d'authentification autorisés, DNS/IP, TLS, tailles, budgets et pagination. Même domaine avec segments numériques différents = **partenaires distincts** jusqu'à preuve contraire.
- Exécuter SSO/MFA manuellement lorsque nécessaire et permis. Ne pas introduire de contournement CAPTCHA, automatisation interdite, accès non autorisé, collecte intensive ni récupération de licence sans permission. Cookies, tokens et URL signées restent dans le contexte privé de la session concernée ; ne pas les inclure dans un ZIP **revu/public**, fixture ni commit.
- Respecter les chemins de données/cache par plugin et les interfaces publiques `PluginContext` ; ne jamais lire des fichiers de profils frères, la base SQLite Core, ni un coffre global fournisseur. Le cache peut être à plusieurs niveaux (public vs compte, mémoire vs disque), mais chaque entrée doit préciser la portée, la fraîcheur, le statut de couverture, l'epoch et la politique d'invalidation.
- Retry seulement pour lectures idempotentes permises, `Retry-After`/429, backoff, annulation, TTL et budgets. Pour les POST et GET de contenu sensible : ne pas rejouer silencieusement après timeout incertain, faute de reçu ou changement de session.
- **Journalisation :** suivre le contrat actuel `docs/LOGGING-CONTRACT.md` et le broker Diagnostics, sans inventer un mécanisme de masquage qui altère le journal brut ; ne jamais expédier automatiquement des credentials, traces privées ou exports vers GitHub/CI/LLM. Les exports manuels bruts sont distincts des rapports publics et nécessitent une décision consciente.

## 6. Démarche autonome après capture Browser Recorder

1. **Lire d'abord** `plugin-dev-kit/README.md`, `plugin-dev-kit/PROVIDER-DEVELOPMENT.md`, `plugin-dev-kit/manifest.json`, `contract-registry.json` et le code du répertoire `contracts/` **de ce ZIP**. Comparer les versions avec le Core installé ; ne pas inventer les signatures absentes. L'exemple `examples/provider-fixture-example` est un `catalog.service` synthétique, **pas** un modèle d'emprunt prêt pour production.
2. Documenter pour **chaque opération** : origine et méthode HTTP, route/paramètres observés, structure des réponses, statut, pagination, couverture, lien au compte, capacité déclarée vs attestée, preuve et inconnues. Un `~route-0001~` anonymisé n'est **jamais** une URL réelle ; une réponse omise n'est pas une absence d'API.
3. Capturer séparément les parcours **public**, **connexion manuelle**, **compte / prêts déjà existants**, **recherche**, **fiche**, **erreur / expiration**. Ne pas inférer la création d'un prêt, le téléchargement ou un droit depuis une simple fiche. Tester les mutations uniquement avec une autorisation et un cas d'essai explicites, jamais automatiquement.
4. Si plusieurs établissements : enregistrer deux captures distinctes, comparer les structures de routes/documents **sans mélanger cookies et identifiants**. Distinguer `tenant_id`, SSO/redirects, quotas, consentements, branche et edition ; encapsuler les variations dans des façades provider de 20–100 lignes environ *si les preuves le permettent* (taille indicative, non normative), moteur HTTP unique.
5. Fournir un **inventaire observé / inféré / inconnu** ; choisir le contrat `provider.library` ou `catalog.service` ; proposer les seules capacités prouvées. Implémenter d'abord l'auth et les lectures non mutatrices, puis la livraison A→B si réellement attestée.
6. Livrer le package, manifeste, fixtures **entièrement fictives**, tests et rapport de limites, sans compte réel ni capture brute dans les sources. Vérifier `python -m pytest` sur tests du plugin et `python scripts/plugin_packaging.py --help` puis commande officielle de build/validation correspondant à la version du dépôt ; exécuter les CI/install/rollback du HEAD exact avant fusion.

### Recette de non-régression minimale

- **Catalogues :** 0/1/N, recherche par champ réellement supporté, pagination 1/N/erreur, filtres serveur vs postfiltre, notices C de deux sources pour une œuvre, champs absents, pas de droit dérivé.
- **Sessions :** deux identités, deux comptes/credentials et deux tenants sous le **même host**, SSO distincts, rebind A→B→A, déconnexion/expiration, concurrentes/annulation, cache isolé ; zéro fuite d'information/permission.
- **Prêts :** zéro/plusieurs prêts, quota inconnu/atteint, état de prêt actif, lecture partielle vs complète, disparition uniquement après inventaire réussi du scope exact ; aucun POST implicite.
- **Livraison :** offre positive avec droit inconnu, droit positif sans offre, 403/409/429, ACSM ≠ LCPL, téléchargement interrompu, octets modifiés, staging invalide, deuxième Sync idempotente sans nouveau GET, OriginLink/Placement dans le bon profil uniquement.
- **Package :** plusieurs descripteurs d'un même bundle installés sans collision, API publique sans import privé, secrets absents des fixtures/PR/ZIP public, tests Core et plugins, smoke GTK ciblé si capacités UI ; aucune dépendance implicite entre plugins.

## 7. Références et limites de la documentation

- **Normatif :** `docs/ARCHITECTURE.md`, `docs/PLUGIN-BOUNDARIES.md`, `src/cifereca/plugin_api/`, `docs/CORE-P0-3719-DOWNLOADABLE-RESOURCE-CONTRACT-V2.md`, `docs/CORE-P1-2385-CATALOGUE-INVENTORY-CONTRACT.md`.
- **Tickets récents :** [#3171](https://github.com/pzia/cifereca/issues/3171) (Sync convergente), [#3719](https://github.com/pzia/cifereca/issues/3719) (référence synthétique), [#2385](https://github.com/pzia/cifereca/issues/2385) (catalogue ≠ bibliothèque), [#4552](https://github.com/pzia/cifereca/issues/4552) (Notice C en cours), [#3724](https://github.com/pzia/cifereca/issues/3724) (adapteurs C), [#3120](https://github.com/pzia/cifereca/issues/3120) (capacité de prêt), [#2703](https://github.com/pzia/cifereca/issues/2703) (matrice), [#2056](https://github.com/pzia/cifereca/issues/2056) et [#3691](https://github.com/pzia/cifereca/issues/3691) (BiblioAccess).
- **Portée :** ce guide ne prétend ni documenter une API privée de fournisseur, ni fournir la totalité du code du Core, ni transformer une capture en contrat d'accès. Une capture Browser Recorder reste **partielle**, volontaire, et le ZIP privé direct **n'est pas anonymisé**. Les contrats exportés du Core installé, leurs déclarations de capacité et les preuves terrain prévalent sur toute suggestion de code.
