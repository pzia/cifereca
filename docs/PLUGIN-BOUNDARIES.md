# Frontières Core / plugins

État documentaire : 7 octobre 2026.

## Consigne Diagnostics prioritaire — 25/10/2026

Le Core et les plugins émettent dans **un journal brut unique**, sans effacer les détails des opérations. Ni le journal, ni la console, ni le broker, ni les exports bruts ne masquent les valeurs (URLs, chemins, comptes, tokens, cookies, titres, etc.). L'IHM Diagnostics est seule à pseudonymiser **les valeurs de login et de mot de passe**, sans masquer d'autre champ. Les tests vérifient la fidélité des valeurs brutes et la pseudonymisation de rendu ; aucun envoi automatique ni dump d'authentification créé uniquement pour les diagnostics. Fichiers locaux 0600, confirmation avant export brut. Ce contrat remplace les anciennes exigences d'expurgation générique des logs. Voir [journal canonique](DIAGNOSTICS_CANONICAL_JOURNAL.md) et [contrat transversal](LOGGING-CONTRACT.md).

## Synchronisation A/B/C — contrat directeur du 28 octobre 2026

**[ARCHITECTURE.md](ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) font foi.** Le Core seul décide et exécute la **Sync automatique unique** : observation de SourceReference exacte (identité/provider/credential/compte/branche/édition), contrôle d'une présence locale par OriginLink attesté → SHA final → Placement vérifié dans le profil, puis nouvelle acquisition dans les options autorisées si non satisfaite. Un scan incomplet n'est pas une absence prouvée et ne bloque pas à jamais une acquisition autrement admise. Le rattrapage manuel #2797 est exceptionnel, pas un préflight universel.

Les plugins produisent les faits fournisseurs/branches et offres séparées, et assurent leur protocole ; ils ne publient pas de droit à partir de Notice C, n'accordent pas eux-mêmes l'origine Core au SHA final et ne touchent pas Storage privé. Le Core publiera atomiquement Artifact/Placement/OriginLink après contenu final et ADEPT éventuel, sans double autorité backup_items/ledger. **La cible n'est pas encore entièrement en production** : snapshots read-only #3252 sont disponibles ; #3171/#3196 doivent livrer l'admission et la persistance convergentes. Les responsabilités Diagnostics plus haut restent inchangées.

## Principe

Une fonctionnalité appartient au Core lorsqu'elle exprime un mécanisme transversal réutilisable par plusieurs plugins ou par l'orchestration générale. Elle appartient au plugin lorsqu'elle encode un protocole, une politique ou un comportement propre à un fournisseur/runtime.

## Ce que porte le Core

Le Core est responsable des primitives communes :

- acquisition générique et orchestration ;
- inspection d'artefacts ;
- publication atomique d'artefacts ;
- cache, TTL et single-flight ;
- journal d'événements canonique, diagnostics, health checks et pseudonymisation des valeurs login/mot de passe **uniquement au rendu IHM** ;
- `TaskBroker`, progression et annulation ;
- transport générique et contrats communs ;
- stockage et ledger ;
- routage/orchestration générique ;
- modèles et API publique nécessaires aux plugins.

Ces primitives doivent être exposées par `cifereca.plugin_api` lorsqu'un plugin doit les consommer.

## Ce que porte un plugin

Un plugin reste responsable de :

- sa logique fournisseur ;
- son protocole ou API fournisseur ;
- ses règles d'authentification/session propres ;
- ses transformations propres ;
- son runtime externe spécialisé ;
- ses mappings métier spécifiques ;
- ses tests de comportement fournisseur.

Une règle ne devient pas Core uniquement parce qu'elle ressemble à une autre : l'équivalence doit être démontrée avant mutualisation.

## Règle d'import

Le code de production d'un plugin ne dépend que de l'API publique `cifereca.plugin_api` pour accéder aux mécanismes Core.

Éviter les imports directs depuis les modules privés `cifereca.services`, `cifereca.storage`, `cifereca.diagnostics`, etc., sauf code appartenant lui-même au Core.

## Exemples rationalisés

### Paris

L'inspection et la publication d'artefacts ont été remontées dans les primitives publiques. Le parcours HTTP/authentifié PNB reste fournisseur-spécifique.

### Gallica

Les régressions OPDS/audio sont couvertes dans `src/plugins/provider-gallica/tests/`. Le Core n'embarque pas les règles métier Gallica.

### Kobo

Le contrat provider est couvert dans `src/plugins/provider-kobo/tests/`. Les mécanismes Kobo spécialisés restent dans le plugin ou dans le runtime dédié.

### Kobo-Adept

Le runtime ADEPT reste séparé du Core : le Core orchestre, le runtime spécialisé réalise l'opération externe.

### Transport HTTP

`transport.http` fournit le transport générique borné, son health check et la publication finale commune. Les sessions authentifiées complexes des providers ne sont pas automatiquement déplacées dans ce transport.

### Diagnostics

La redaction commune est une primitive publique. Les messages et contextes métier restent du ressort du provider.

## Décision pour une nouvelle fonctionnalité

Avant d'ajouter du code à un plugin :

1. vérifier si une primitive publique existe déjà ;
2. vérifier si le besoin est réellement transversal ;
3. mutualiser seulement le mécanisme commun minimal ;
4. garder les règles fournisseur dans le plugin ;
5. ajouter un test de contrat plugin si une nouvelle primitive Core est destinée aux plugins.

Voir également [ARCHITECTURE.md](ARCHITECTURE.md) et [TEST-ORGANIZATION.md](TEST-ORGANIZATION.md).


### Déclarations du produit commercial en détail (#1886)

Un provider peut fournir `SearchResult.metadata.public_product_metadata` pour
les libellés **effectivement publiés** d'un produit commercial. Le Core affiche
uniquement `brand`, `collection`, `publication_date`, `pagination_label`,
`language_label` et `selected_format_label`, sous le préfixe « Fiche publique ».
Les valeurs restent bornées et filtrées comme les autres métadonnées ; leurs
actions permettent uniquement la copie. Aucun droit, identifiant, format de
l'achat ou âge ne se déduit de ce namespace. Les champs inconnus et les
structures internes ne sont pas affichés. La pagination conserve la mention
« support non précisé » et le format la mention « commercial sélectionné ».

Le plugin possède la validation de sa source et conserve sa provenance exacte
séparément ; le Core ne lit ni HTML ni preuve spécifique au fournisseur et
n'effectue aucun réseau pour présenter ces valeurs. Cette projection n'active
ni enrichissement, ni source, ni acquisition et n'écrase pas les métadonnées
privées/EPUB. Un plugin peut laisser le namespace absent si la preuve manque.


### Observation de performances des pages techniques (#2139)

Le contrat optionnel public `UiPerformanceHost` fournit
`performance_report()` et `set_performance_capture(enabled: bool)`.
Il délègue au **même** moniteur Core que les contrôles de secours des
Paramètres ; lire un rapport ou construire une page n’active rien.
Le rapport `cifereca.performance.phases/1` est local, borné et expurgé
par le moniteur. Le choix d’un fichier et son export restent des actions
explicites de la page, sans upload ni nouveau collecteur.

Une ancienne page dont le host est périmé ou une fenêtre fermée ne peut
plus modifier la capture. Un nouveau host retrouve la capture existante,
qui persiste à la navigation jusqu’à arrêt explicite ou fermeture Core.
Les plugins vérifient cette extension optionnelle et indiquent son
indisponibilité sur un ancien Core, sans importer le moniteur privé.
