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

## Synchronisation A/B/C — cible en cours

- **Déjà observables** : contrats de droits en lecture seule et adaptateurs Kobo/Numilog/Paris/BnF (#3252) ; tables historiques entitlements, artifacts, placements, acquisition_ledger et backup_items. Les lectures ne valent ni permission d'acquisition ni OriginLink publié.
- **P0 en cours** : #3171 moteur agnostique, #3196 autorité SQL unique (sortie de backup_items), #2797/#2847 contrôles de présence et chemins, #3185 mutations et #3275 transferts. La PR #3528 (admission de lecture explicitement scopée) est **fusionnée** ; #3523 (plan en mémoire) est **ouverte**. Ni l'une ni l'autre ne livre la convergence transactionnelle.
- **Règle** : synchronisation automatique **unique** A→B ; lorsque la preuve OriginLink/SHA/Placement local manque, une acquisition reste possible sous admission indépendante, droits/consentements/contrôles. Le rattrapage manuel est exceptionnel, pas un deuxième processus obligatoire.

## Simulation

simulation/ **existe** et reste le banc fonctionnel de référence. Aucune migration de ce répertoire vers lui-même n'est requise ; conserver ses services et fixtures utiles sans seconde arborescence. Voir [SIMULATION.md](SIMULATION.md).

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
2. décider et exécuter la migration `simulation/` → `simulation/` ;
3. poursuivre la rationalisation de `builtin_ui` et Kobo dans des PR de code séparées ;
4. traiter le déplacement physique des plugins seulement avec packaging/tests adaptés ;
5. nettoyer la hiérarchie legacy Recommendations ;
6. enrichir éventuellement l'inspection EPUB Core avant toute suppression du validateur strict Numilog.
