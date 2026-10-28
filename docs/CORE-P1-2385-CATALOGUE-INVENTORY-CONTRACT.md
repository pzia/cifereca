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
