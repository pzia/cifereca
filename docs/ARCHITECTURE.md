# Architecture CIFERECA — état actuel, cible et rationalisation

État documentaire : 11 octobre 2026.

Ce document décrit la structure du dépôt. Il distingue volontairement **l'état réel actuel**, **la cible structurelle** et **les travaux encore nécessaires**.

## 1. État actuel

### Core et framework

Le code applicatif Core est sous `src/cifereca/`.

Zones structurantes :

```text
src/cifereca/
├── core/           # runtime et primitives Core
├── plugin_api/     # API publique stable destinée aux plugins
├── plugins/        # framework/loader/manager de plugins
├── gui/            # shell et primitives UI Core
├── builtin_ui/     # UI historique encore intégrée au Core
├── kobo/           # orchestration Kobo Core résiduelle ; client Vault provider-owned
└── ...
```

Point important : `src/cifereca/plugins/` ne contient pas les plugins fonctionnels. Il contient l'infrastructure qui les charge, résout leurs dépendances et gère leur état.

### Plugins fonctionnels

Les plugins fonctionnels sont versionnés sous `src/plugins/` :

```text
src/plugins/
├── provider-paris/
├── provider-kobo/
├── provider-gallica/
├── provider-gutenberg/
├── provider-bnf-catalogue/
├── provider-numilog/
├── transport-http/
├── recommendations/
└── ...
```

Chaque package possède son manifeste `cifereca-plugin.json`, son code et ses tests spécifiques.

### Zones retirées de la source active

`bundled_plugins/` et `integrations/` ne font plus partie de la source active du tree courant. Les références historiques à ces zones ne doivent pas guider de nouveau développement.

## 2. Cible structurelle

La cible envisagée est :

```text
src/
├── cifereca/
│   ├── core/
│   ├── plugin_api/
│   ├── plugins/       # framework/loader/manager uniquement
│   ├── gui/
│   └── ...
└── plugins/           # plugins fonctionnels
```

Cette cible est désormais matérialisée : les plugins fonctionnels résident sous `src/plugins/`. Le répertoire racine `plugins/` n'est plus une source active.

## 3. Frontière Core / plugins

Principe : le Core fournit les mécanismes transversaux ; les plugins portent la connaissance fournisseur ou le runtime spécialisé.

Voir [PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md) pour les règles détaillées.

## 4. Tests

- `tests/` : Core, contrats transversaux et orchestration générique.
- `src/plugins/<plugin>/tests/` : comportement propre à un plugin.
- `simulation/tests/` : validation du banc de simulation actuel.
- `environnements/**` : self-tests d'environnement/runtime.

Voir [TEST-ORGANIZATION.md](TEST-ORGANIZATION.md).

## 5. Simulation et environnements

- `simulation/` : simulation fonctionnelle **actuelle et canonique**, avec faux services, fixtures et tests ; aucune seconde arborescence cible.
- `environnements/` : recettes machine/runtime, distinctes des faux services fonctionnels.

Voir [SIMULATION.md](SIMULATION.md).

## 6. Travaux structurels encore nécessaires

Les sujets suivants restent hors du présent lot documentaire :

1. maintenir `src/plugins/` comme unique source canonique des plugins fonctionnels ;
2. extraire ou supprimer progressivement `src/cifereca/builtin_ui/` ;
3. maintenir le client HTTP/PNB Paris exclusivement dans `src/plugins/provider-paris/` (orchestration générique d'acquisition et de restitution dans le Core) ;
4. extraire le code fournisseur encore présent sous `src/cifereca/kobo/` ;
5. rationaliser les faux services et fixtures **dans** `simulation/`, sans migration vers un chemin identique ;
6. poursuivre la rationalisation des tests historiques sans restauration transitive ;
7. supprimer le code historique uniquement dans des PR dédiées après preuve d'équivalence.

L'état détaillé est suivi dans [RATIONALIZATION-STATUS.md](RATIONALIZATION-STATUS.md).
