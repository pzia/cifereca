# Architecture CIFERECA — état actuel, cible et rationalisation

État documentaire : 7 octobre 2026.

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
├── paris/          # code historique Paris encore présent
├── kobo/           # code historique Kobo encore présent
└── ...
```

Point important : `src/cifereca/plugins/` ne contient pas les plugins fonctionnels. Il contient l'infrastructure qui les charge, résout leurs dépendances et gère leur état.

### Plugins fonctionnels

Les plugins fonctionnels sont actuellement versionnés à la racine :

```text
plugins/
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
└── plugins/           # cible future des plugins fonctionnels
```

Cette cible n'est **pas encore matérialisée** dans le dépôt : les plugins fonctionnels restent aujourd'hui sous `plugins/` à la racine.

Le déplacement physique doit être traité dans une PR structurelle distincte, avec adaptation du packaging et des chemins de test. Le présent lot documentaire ne déplace aucun code.

## 3. Frontière Core / plugins

Principe : le Core fournit les mécanismes transversaux ; les plugins portent la connaissance fournisseur ou le runtime spécialisé.

Voir [PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md) pour les règles détaillées.

## 4. Tests

- `tests/` : Core, contrats transversaux et orchestration générique.
- `plugins/<plugin>/tests/` : comportement propre à un plugin.
- `simulation/tests/` : validation du banc de simulation actuel.
- `environnements/**` : self-tests d'environnement/runtime.

Voir [TEST-ORGANIZATION.md](TEST-ORGANIZATION.md).

## 5. Simulation et environnements

- `simulation/` : simulation fonctionnelle actuelle.
- `simulation/` : cible future, actuellement absente.
- `environnements/` : recettes machine/runtime, distinctes des faux services fonctionnels.

Voir [SIMULATION.md](SIMULATION.md).

## 6. Travaux structurels encore nécessaires

Les sujets suivants restent hors du présent lot documentaire :

1. déplacer physiquement les plugins fonctionnels vers la cible retenue ;
2. extraire ou supprimer progressivement `src/cifereca/builtin_ui/` ;
3. extraire le code fournisseur encore présent sous `src/cifereca/paris/` ;
4. extraire le code fournisseur encore présent sous `src/cifereca/kobo/` ;
5. créer effectivement `simulation/` et y migrer les éléments utiles de `simulation/` ;
6. poursuivre la rationalisation des tests historiques sans restauration transitive ;
7. supprimer le code historique uniquement dans des PR dédiées après preuve d'équivalence.

L'état détaillé est suivi dans [RATIONALIZATION-STATUS.md](RATIONALIZATION-STATUS.md).
