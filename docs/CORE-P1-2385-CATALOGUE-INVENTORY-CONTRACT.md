# #2385 — contrat Core v1 : catalogue ≠ inventaire personnel ≠ acquisition

> **Document historique/partiel — 28/10/2026.** capacité catalogue en lecture seule, non preuve de prêt/achat/fichier. La cible normative est la **synchronisation automatique unique** décrite dans [ARCHITECTURE.md](ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) ; le rattrapage manuel #2797 reste exceptionnel. Conserver les gardes existants ; ne pas déduire qu'un OriginLink canonique/transaction Core est livré de cette seule recette.

## Périmètre effectivement livré

`LibraryService.catalogue_source_capabilities(identity_id=None)` est une
projection **read-only / cache-only** des fournisseurs réellement présents dans
le registre Core, prêts et activés **pour l'identité choisie**.
`CatalogBrokerImpl.catalogue_source_capabilities(identity_id=None)` expose
la même méthode en **extension additive facultative** du broker existant
`catalog`. Le modèle **`CatalogueSourceCapability`** et le protocole
optionnel **`CatalogueSourceInventoryBroker`** s'importent uniquement depuis
`cifereca.plugin_api` ; les consommateurs testent cette capacité à
l'exécution avant usage. La signature du `CatalogBroker` historique et
celle du `SynchronizationBroker.sources` sont **inchangées**.

Schéma v1 : `CatalogueSourceCapability`, immutable, uniquement :
- `provider_id` : slug canonique enregistré, sans alias/compte privé ;
- `identity_scope` : `public` ou `authenticated` (déclaratif) ;
- `catalog_search_supported` : provider ready et capacité `search=True` ;
- `catalog_search_available` : en plus, **session du bon provider/compte
  réellement connectée** (pour l'identité demandée) ;
- `catalog_refresh` : **toujours `unsupported` pour les plugins actuels** ;
- `personal_inventory_supported` : `loans=True` ou
  `personal_library=True` déclaré, **sans** inférence de droits ;
- `personal_inventory_available` : en plus un compte de l'identité
  concernée et sa session connectée ;
- `acquisition=separate_explicit` : jamais déclenchée ou autorisée ici ;
- `schema_version=1`.

Les plugins de transformation/techniques, désactivés, skeleton, dont l'identité
est masquée ou sans capacité pertinente sont exclus. Une source pouvant
rechercher mais déconnectée n'est **pas annoncée opérationnelle**. Les
sessions anonymes publiques ne nécessitent **aucun pseudo Account**.
L'inventaire n'effectue aucune recherche, aucun GET, scan SQLite, appel
`sync`, téléchargement, acquisition, prêt, retour ni renouvellement.
Les clés `credential_id` sont seulement comparées en mémoire, jamais renvoyées.

## Matrice de capacités — exemples, pas une preuve de santé réseau

| Famille | Recherche ponctuelle | Sync catalogue déclaré v1 | Inventaire personnel |
| --- | --- | --- | --- |
| Gallica/Gutenberg/EbooksGratuits | seulement si plugin activé + capacité `search` | **non pris en charge** | non |
| BnF catalogue/Baen | selon plugin réellement installé/activé | **non pris en charge** | selon preuve (pas par défaut) |
| Kobo catalogue | `search` si activé, session valide | **non pris en charge** | `personal_library`, compte/identité distincts |
| Numilog catalogue public (désactivé par défaut) | **absent si désactivé** | **non pris en charge** | sans objet |
| Numilog/Kobo achats | recherche possible | **non pris en charge** | `personal_library` si compte connecté |
| Paris et autres prêts | éventuellement `search` | **non pris en charge** | `loans` si compte connecté |
| Plugin technique | absent | absent | absent |

La présence de pagination, de flux OPDS, d'un cache miroir local, de
`holdings`, d'un `direct_download` ou d'une pseudo Account **ne suffit
pas** pour revendiquer `catalog.refresh`, `catalog.snapshot` ou
`catalog.incremental`. Pour les prochaines capacités, il faudra un **contrat
déclaré, versionné ET implémenté avec protocole, budget, provenance,
fraîcheur, cache autorisé, consentement explicite**, et des tests du plugin.
Aucun moteur de refresh catalogue n'est introduit par ce lot.

## Handoff CoreUI / Plugins / Backlog

- **CoreUI** : section « Catalogues » basée sur l'inventaire Core,
  « Bibliothèques personnelles » basées sur le Sync Broker existant,
  filtres/actions selon `catalog_search_available` et
  `personal_inventory_available` ; état explicite « recherche uniquement,
  synchronisation d'index non prise en charge ». Pas de faux switch « sync ».
- **Plugins** : déclaration/implémentation *explicite* ultérieure d'un
  rafraîchissement de catalogue réellement supporté, avec récits de cache,
  conditions d'utilisation et reprise après interruption.
- **Core/Backlog** : modèle futur des plans et résultats de sync catalogue
  (`success/partial/unsupported/error`, timestamps, budgets, tombstones)
  et harmonisation UI ; ne pas attribuer de preuve réseau à ce contrat v1.

Ce lot respecte les frontières #2361 (sélecteur de Recherche), #1917
(capacités de recherche), #2370/#2380 (diagnostics), #1120 (API stable).
Aucune réactivation d'un plugin archivé ni collecte implicite.

## Surfaces multiples et provenance documentaire (#4030)

La capacité v1 ne classe pas un fournisseur dans une catégorie métier exclusive et ne confère aucun accès distant ni consentement à l'outil de suggestion. Les familles et limites A/B/C sont spécifiées dans [CORE-P0-4030-ORIGIN-SURFACE-TAXONOMY.md](CORE-P0-4030-ORIGIN-SURFACE-TAXONOMY.md). Les futurs résultats par surface exigent pagination, fraîcheur, preuve de compte et budget, sans créer de droit ou d'OriginLink.


## Critères minimaux de qualification d'une future actualisation (proposition, non ABI)

La recherche ponctuelle reste distincte de toute opération d'actualisation.
Tant qu'un plugin ne fournit pas un contrat approuvé et testé, son état
demeure `unsupported`. Les éléments suivants sont des exigences de
réception, pas des capacités implémentées ni des noms d'API gelés.

| Élément | Preuve attendue |
| --- | --- |
| Autorité | Identifiant canonique du plugin, version du contrat, périmètre exact du catalogue |
| Déclenchement | Manuel, démarrage ou périodique explicitement déclaré ; consentement distinct et révocable avant toute requête |
| Budget | Limites finies de pages, requêtes, durée et octets de cache ; arrêt à la première limite |
| Accès | Conditions de service, licence de cache, respect de 401/403/429 ; aucun contournement |
| Partition | Public partagé seulement si attesté public ; privé partitionné par compte réel et accès autorisé |
| Résultat | success seulement pour le périmètre effectivement complet ; partial pour interruption, budget ou couverture inconnue ; unsupported sans appel ; error pour échec |
| Fraîcheur | last_attempt distinct de last_success ; partial/error ne renouvellent pas la preuve de complétude |
| Suppression | Tombstone seulement sur preuve positive qualifiée ; absence dans une page ou expiration de cache insuffisante |
| Effets | Aucun prêt, achat, téléchargement, effacement de droit ou de fichier local |

L'agrégation doit conserver les résultats individuels : un mélange de succès
et de couverture partielle ne constitue pas un succès exhaustif. Une source
non prise en charge n'est ni un échec réseau ni une preuve de catalogue vide.
La recette de contrat peut être effectuée sur fixtures ; la recette réseau,
les licences et le consentement réel restent des preuves distinctes à fournir
par le propriétaire du plugin.

### Réception incrémentale automatisée

- #7923 : activation Baen par identité, reconnexion Numilog et absence
  d'acquisition implicite ; fusion `8683ee42`, PR CI 45330242748 verte.
- #7914 : états partiels, budget et erreurs Core distincts du zéro qualifié.
- #7919 : chronologie globale avant pagination, rétention et redémarrage.
- #7920 : provenance et fraîcheur des observations d'âge.

Ces preuves ne qualifient aucune nouvelle capacité réseau et ne remplacent
pas les arbitrages #4552 ni la recette terrain.
