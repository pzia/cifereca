# État de rationalisation CIFERECA

État de référence documentaire : 11 octobre 2026.

Ce fichier est l'index de référence pour la rationalisation. Les handoffs historiques restent utiles pour leur contexte, mais ne doivent pas être lus comme une description automatiquement à jour du tree courant.

## Rationalisation terminée

### Frontières et primitives Core/plugins

Le chantier transversal de mutualisation est clos :

- `TaskBroker` public ;
- présentation maître/détail publique ;
- `transport.http` ;
- inspection PDF/EPUB/ACSM publique ;
- publication atomique de contenu et de fichiers ;
- health checks / diagnostics publics ;
- CacheBroker TTL + single-flight ;
- normalisation langue, noms de fichiers et descriptions ;
- redaction diagnostics publique.

Référence : [HANDOFF-MUTUALISATION-CORE-PLUGINS.md](../HANDOFF-MUTUALISATION-CORE-PLUGINS.md).

### Tests historiques déjà répartis

Ne pas restaurer en bloc :

- `test_sync_082.py` ;
- `test_kobo_vault.py` ;
- anciens scénarios Gallica de `test_gallica_plugin.py` ;
- anciens scénarios updater de `test_update.py` ;
- ancienne variante historique de `test_095_consolidation.py`.

La répartition actuelle est décrite dans [TEST-ORGANIZATION.md](TEST-ORGANIZATION.md).

## Rationalisation en cours

La modularisation P0 de `src/cifereca/services.py` et `src/cifereca/gui/main_window.py` est suivie par les épics [#1049](https://github.com/pzia/cifereca/issues/1049) et [#1051](https://github.com/pzia/cifereca/issues/1051), et leurs lots #1116–#1132. Aucun découpage monolithique simultané Core/CoreUI.


### Triage des tests historiques 0.9.21 → 0.9.33

À analyser méthode par méthode, sans restauration transitive :

- `test_core_0921.py` ;
- `test_core_0923.py` ;
- `test_core_0925.py` ;
- `test_core_0927.py` ;
- `test_core_0929.py` ;
- `test_core_0931.py` ;
- `test_core_0933.py`.

Ces fichiers historiques ne sont pas présents dans le tree courant à la date de ce document. Le chantier consiste à récupérer leurs invariants utiles depuis leur source/historique puis à les replacer correctement.

## Dette structurelle restante

### Core / plugins

- `src/cifereca/builtin_ui/` reste intégré au Core.
- `src/cifereca/kobo/` conserve seulement l'orchestration résiduelle Core ; le client HTTP Kobo-Vault appartient au plugin provider-kobo.
- les plugins fonctionnels sont désormais sous `src/plugins/`, source canonique unique ;
- le répertoire racine `plugins/` n'est plus une source active.

Voir [ARCHITECTURE.md](ARCHITECTURE.md).

### Recommendations

L'entrypoint actif utilise les primitives publiques actuelles, mais la hiérarchie conserve du code legacy de threading dans une base historique. Nettoyage à traiter séparément.

### Numilog EPUB strict

Le validateur local reste plus strict que l'inspection Core sur plusieurs invariants ZIP/OPF/protection. Ne pas le supprimer avant équivalence démontrée.

### Sessions HTTP fournisseur

Paris est désormais entièrement porté par `src/plugins/provider-paris/`. Numilog, Kobo et d'autres providers peuvent garder une session HTTP spécialisée lorsque l'authentification, les cookies ou la navigation métier dépassent le contrat générique `transport.http`.

## Simulation

`simulation/` est **l'unique banc fonctionnel existant et la cible canonique**. Les anciens paragraphes décrivant une migration `simulation/` → `simulation/` étaient contradictoires ; aucune copie ni suppression globale de ce répertoire n'est requise.

Rationaliser les jeux de données et faux services **à l'intérieur** du banc existant, en conservant tests, consommateurs et preuve de couverture. Les dépendances OS/GTK appartiennent à `environnements/`.

Voir [SIMULATION.md](SIMULATION.md).

## Environnements

`environnements/` décrit des recettes machine/runtime, pas une seconde simulation :

- `openai-chat-debian/` : recette Debian/OpenAI GTK de référence ;
- `chatgpt-work/` : recette de travail Ubuntu/ChatGPT Work.

## Index des handoffs

### Encore utiles comme contexte

- [HANDOFF-MUTUALISATION-CORE-PLUGINS.md](../HANDOFF-MUTUALISATION-CORE-PLUGINS.md) — clôture des primitives mutualisées.
- [HANDOFF-GEN-DATASET.md](../HANDOFF-GEN-DATASET.md) — état détaillé du banc `simulation`, à lire avec la cible `simulation/`.
- [HANDOFF-PLUGINS-FROM-CORE044-DEV1.md](../HANDOFF-PLUGINS-FROM-CORE044-DEV1.md) — contexte de la vague plugins/Core044.

### Historiques / non autoritatifs pour l'état courant

- [HANDOFF-RECOMMENDATIONS.md](../HANDOFF-RECOMMENDATIONS.md) — état d'une version antérieure du plugin ; utile pour l'historique performance/algorithmes.
- [HANDOFF-UX-CORE-PLUGINS.md](../HANDOFF-UX-CORE-PLUGINS.md) — analyse/plan UX ; plusieurs éléments y sont explicitement des propositions, pas des décisions d'architecture.

En cas de contradiction, l'état réel du tree, [ARCHITECTURE.md](ARCHITECTURE.md), [PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md) et [TEST-ORGANIZATION.md](TEST-ORGANIZATION.md) priment.

## Prochaines étapes de rationalisation

1. poursuivre le triage historique `0921 → 0933` ;
2. consolider les fixtures et faux services déjà dans `simulation/` ;
3. poursuivre la rationalisation de `builtin_ui` et Kobo dans des PR de code séparées ;
4. conserver les plugins fonctionnels sous `src/plugins/` comme source unique et ne déplacer un package que si packaging/tests le requièrent ;
5. nettoyer la hiérarchie legacy Recommendations ;
6. enrichir éventuellement l'inspection EPUB Core avant toute suppression du validateur strict Numilog.
