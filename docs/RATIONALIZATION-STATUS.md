# État de rationalisation CIFERECA

État de référence documentaire : **28 octobre 2026**. Voir [ARCHITECTURE.md](ARCHITECTURE.md), décision normative [#3171](https://github.com/pzia/cifereca/issues/3171). Les composants mutualisés décrits comme terminés ci-dessous ne signifient **pas** que la convergence achats/prêts A→B, la migration de backup_items ou les huit OriginLink persistés soient livrés.

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
- journal brut Diagnostics et pseudonymisation de login/mot de passe au seul rendu IHM (voir [LOGGING-CONTRACT.md](LOGGING-CONTRACT.md)).

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

- `builtin_ui` reste dans CoreUI : [#157](https://github.com/pzia/cifereca/pull/157) sépare pages, chrome, système et maintenance cache ; shim et entrypoints publics stables ([contrats](../tests/test_builtin_ui_rationalization.py)).
- Installation CLI [#108](https://github.com/pzia/cifereca/pull/108) : `resolve_archive` privilégie le chemin fourni, puis Téléchargements/XDG pour un nom simple ; chemins explicites sans fallback, erreur listant les essais ([tests](../tests/test_install_helpers_097.py)).
- **Rationalisations historiques Paris/Kobo — capitalisation du 27/11/2026 :** [#164](https://github.com/pzia/cifereca/pull/164) a supprimé la zone Paris du Core et déplacé les invariants de normalisation, session/retry, recherche résiliente et prêts dans les tests provider Paris ; [#182](https://github.com/pzia/cifereca/pull/182) a supprimé le client HTTP Kobo-Vault du Core, en conservant à cette date l'orchestration résiduelle et la compatibilité nécessaire. Au HEAD `ee0978a02902118005ffa2b14f0051106c82f2c7`, les deux répertoires `src/cifereca/paris/` et `src/cifereca/kobo/` sont absents. Ne pas restaurer ces anciennes zones : protocoles, mappings et sessions restent chez le provider, runtime ADEPT spécialisé séparé, mécanismes transversaux et décision A→B au Core selon [ARCHITECTURE.md](ARCHITECTURE.md) et [PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md). Témoins relus : [résilience Paris](../src/plugins/provider-paris/tests/test_legacy_client_resilience.py), [normalisation Paris](../src/plugins/provider-paris/tests/test_legacy_normalize_contract.py) et [frontière Kobo](../tests/test_kobo_boundary_rationalization.py). Les attributs historiques de restitution ou les anciennes orchestrations ne prescrivent aucun retour volontaire ni réactivation de W2. Relecture statique, sans nouvelle recette PNB/ACSM, ADEPT ni attestation exhaustive de découplage.
- depuis la PR #201, `src/plugins/` est la seule source canonique ; `plugins/`, `bundled_plugins/` et `integrations/` sont absents au HEAD relu.

Voir [ARCHITECTURE.md](ARCHITECTURE.md).

### Recommendations

L'entrypoint actif utilise les primitives publiques actuelles, mais la hiérarchie conserve du code legacy de threading dans une base historique. Nettoyage à traiter séparément.

### Numilog EPUB strict

Le validateur local reste plus strict que l'inspection Core sur plusieurs invariants ZIP/OPF/protection. Ne pas le supprimer avant équivalence démontrée.

### Sessions HTTP fournisseur

Paris est désormais entièrement porté par `src/plugins/provider-paris/`. Numilog, Kobo et d'autres providers peuvent garder une session HTTP spécialisée lorsque l'authentification, les cookies ou la navigation métier dépassent le contrat générique `transport.http`.

## Synchronisation A/B/C — cible en cours

- **Déjà observables** : contrats de droits en lecture seule et adaptateurs Kobo/Numilog/Paris/BnF (#3252) ; tables historiques entitlements, artifacts, placements, acquisition_ledger et backup_items. Les lectures ne valent ni permission d'acquisition ni OriginLink publié.
- **P0 en cours** : #3171 moteur agnostique, #3196 autorité SQL unique (sortie de backup_items), #2797/#2847 contrôles de présence et chemins, #3185 mutations et #3275 transferts. La PR #3528 (admission de lecture explicitement scopée) est **fusionnée** ; #3523 (plan en mémoire) est **ouverte**. Ni l'une ni l'autre ne livre la convergence transactionnelle.
- **Règle** : synchronisation automatique **unique** A→B ; lorsque la preuve OriginLink/SHA/Placement local manque, une acquisition reste possible sous admission indépendante, droits/consentements/contrôles. Le rattrapage manuel est exceptionnel, pas un deuxième processus obligatoire.

## Simulation

La PR #148 a migré `gen-dataset/` vers `simulation/`, seul banc fonctionnel de référence ; l'ancien répertoire est absent au HEAD relu. Voir [SIMULATION.md](SIMULATION.md).

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
2. réexaminer les résidus Core/provider selon les contrats courants, sans rouvrir les migrations builtin_ui/Paris/Kobo ;
3. nettoyer la hiérarchie legacy Recommendations ;
4. enrichir éventuellement l'inspection EPUB Core avant toute suppression du validateur strict Numilog.
