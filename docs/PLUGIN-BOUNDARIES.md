# Frontières Core / plugins

État documentaire : 7 octobre 2026.

## Principe

Une fonctionnalité appartient au Core lorsqu'elle exprime un mécanisme transversal réutilisable par plusieurs plugins ou par l'orchestration générale. Elle appartient au plugin lorsqu'elle encode un protocole, une politique ou un comportement propre à un fournisseur/runtime.

## Ce que porte le Core

Le Core est responsable des primitives communes :

- acquisition générique et orchestration ;
- inspection d'artefacts ;
- publication atomique d'artefacts ;
- cache, TTL et single-flight ;
- diagnostics, health checks et redaction ;
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
