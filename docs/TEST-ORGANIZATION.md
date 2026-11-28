# Organisation des tests CIFERECA

État documentaire : 7 octobre 2026.

## Règle de placement

Le test est placé selon **ce qu'il affirme**, pas selon les dépendances qu'il utilise.

### `tests/`

Réservé à :

- comportement Core ;
- contrats transversaux ;
- orchestration générique ;
- interactions entre plusieurs sous-systèmes Core ;
- invariants publics qui ne sont pas propres à un provider.

### `src/plugins/<plugin>/tests/`

Réservé à :

- parsing fournisseur ;
- session/authentification fournisseur ;
- mapping fournisseur ;
- règles catalogue/acquisition propres ;
- régressions spécifiques au plugin ;
- contrats publics vus depuis ce plugin.

### `simulation/tests/`

Valide le banc de simulation **actuel** : faux services, profils et orchestration de dataset. Ces tests ne remplacent ni les tests Core ni les tests fournisseur.

### `environnements/`

Les self-tests valident les recettes machine/runtime : GTK, Xvfb, D-Bus, bootstrap, capture. Ils ne certifient pas à eux seuls le comportement fonctionnel de Cifereca.

## Règles de rationalisation historique

- ne jamais restaurer en bloc les anciens tests `_bundled` ;
- migrer les invariants historiques méthode par méthode ;
- ne pas créer de chaînes où de nouveaux tests importent d'anciens tests comme dépendances ;
- lorsqu'un ancien test mélange Core et fournisseur, répartir sa couverture selon la frontière actuelle ;
- supprimer/restaurer un test uniquement après avoir identifié l'invariant qu'il protège.

## Historique clôturé : ne pas restaurer en bloc

Les sources historiques suivantes sont considérées comme rationalisées ou remplacées. Elles ne constituent pas une liste de fichiers à réintroduire :

- `test_095_consolidation.py` historique ;
- `test_sync_082.py` ;
- `test_kobo_vault.py` ;
- anciens scénarios Gallica de `test_gallica_plugin.py` ;
- anciens scénarios updater de `test_update.py`.

Le tree courant contient encore `tests/test_095_consolidation.py` sous forme de couverture Core active issue de la restauration/rationalisation 0.9.2. Cette présence ne justifie pas de restaurer une ancienne variante du module en bloc.

## Couverture issue de `test_sync_082.py`

La couverture utile est désormais répartie entre :

- `tests/test_sync_state_contract.py` ;
- `src/plugins/provider-paris/tests/test_sync082_regressions.py` ;
- `tests/test_adept_profile_readiness.py` ;
- `tests/test_adept_artifact_orchestration.py` ;
- `tests/test_kobo_sync_modes.py` ;
- les tests Core ADEPT existants.

L'ancien module `test_sync_082.py` n'est pas présent dans le tree actif et ne doit pas être restauré en bloc.

## Couverture issue de `test_kobo_vault.py`

La couverture utile est répartie entre :

- `src/plugins/provider-kobo/tests/test_vault_contract.py` ;
- `tests/test_kobo_executor_retries.py` ;
- `tests/test_kobo_backup_fault_isolation.py` ;
- `tests/test_kobo_backup_observability.py` ;
- `tests/test_kobo_backup_reconcile_prune.py` ;
- `tests/test_kobo_executor_policy.py` ;
- `tests/test_kobo_executor_kdrm.py` ;
- `tests/test_kobo_archive_metadata.py` ;
- `tests/test_kobo_adept_state.py` ;
- `tests/test_kobo_vault_closure.py`.

L'ancien `test_kobo_vault.py` n'est pas présent dans le tree actif.

## Rationalisation Core 0.9.21 — capitalisation du 27 novembre 2026

Le plan historique [#103](https://github.com/pzia/cifereca/issues/103), incluant [#102](https://github.com/pzia/cifereca/issues/102), a été livré par les PR [#104](https://github.com/pzia/cifereca/pull/104), [#107](https://github.com/pzia/cifereca/pull/107), [#106](https://github.com/pzia/cifereca/pull/106) et [#109](https://github.com/pzia/cifereca/pull/109). Ne pas restaurer `test_core_0921.py` en bloc : les lots ont été distribués dans les tests spécialisés. La livraison historique n'établit pas à elle seule la couverture actuelle.

Relecture des sources de tests au commit `e8ed298116e57d44645c37cd3fa7ad91e1e9077e` (sans nouvelle exécution des suites) :

- [Inventaire ADEPT](../tests/test_adept_inventory_binding.py) : binding unique attesté et profils explicitement résolus, refus des ambiguïtés et inventaires partiels, conservation du binding et du fichier si l'entrée disparaît, revalidation de l'état returned, aucun appel d'inventaire si artefact absent ou verrou occupé, réutilisation de l'inventaire par combinaison de profils lors du batch.
- [Retour ADEPT](../tests/test_adept_return.py) : les anciens critères de confirmation par refresh d'inventaire, retour non vérifié ou acquisition inconnue sont historiques. Depuis la décision [#4048](https://github.com/pzia/cifereca/issues/4048), la restitution volontaire est retirée : refus `loan_return_removed` avant accès runtime ou mutation, lookup inerte, conservation de l'artefact et de l'état historique. Ne pas réintroduire une restitution pour satisfaire un ancien test.
- [UI ADEPT](../tests/test_adept_ui_contract.py) : réconciliation depuis le détail sur worker ; l'ancienne action de retour et son message de confirmation sont retirés, sans retirer la réconciliation. Aucun succès de retour ne doit être fabriqué.
- [Kobo guard](../tests/test_kobo_backup_guard.py) : les six critères historiques étaient blocage du fichier non lié, permission explicite sans écrasement, exclusion des autres identités et des fichiers absents du blocage, conservation du backup lié complet (`unchanged`), scan préalable. L'ancien exécuteur W2 est désormais retiré ; les tests relus protègent cette absence et l'absence de mutation des candidats locaux hostiles, pas la réactivation de ces six chemins. L'admission et la publication A→B suivent le [contrat canonique](ARCHITECTURE.md) : preuve de présence exacte, consentement et contrôles frais, non-écrasement et séparation des profils ; ne pas restaurer un veto legacy sur la seule présence locale similaire. Les suites de publication atomique/reprise sont la référence pour le parcours canonique.

Les invariants historiques devenus obsolètes sont conservés comme décisions datées, non comme spécification exécutable. Le module historique est absent du tree relu.

## Rationalisation Core 0.9.23 — capitalisation du 27 novembre 2026

Le plan historique [#110](https://github.com/pzia/cifereca/issues/110) a été livré par les PR [#111](https://github.com/pzia/cifereca/pull/111), [#123](https://github.com/pzia/cifereca/pull/123) et [#129](https://github.com/pzia/cifereca/pull/129). Relecture des sources de tests au commit `f31515bedacf7d9ef91a7c7c7ec77a76439044db`, sans nouvelle exécution des suites :

- GTK-C09 — [focus GTK](../tests/test_core0923_gtk_focus.py) : le contrôleur réel, avec témoin GTK injecté, ne demande pas de focus sans racine Gtk.Window ; racine valide → TAB_FORWARD, garde de démontage locale, aucun masquage global des warnings. Cette preuve ne remplace pas une recette GTK réelle.
- IDENT-C08 — [sauvegarde identité](../tests/test_identity_save_ui_contract.py) : rafraîchir sélecteurs/délégations/Accueil uniquement au succès, préserver l'identité active au succès et à l'échec.
- ADE-C07 — [rattachement ADEPT](../tests/test_adept_manual_binding.py) : sélection initiale vide, choix explicite profil/prêt, relecture et ID exact, ancien titre/chemin non probants, conservation de l'artefact et de la liaison précédente, `binding_source=manual_selection`, aucun téléchargement ni retour implicites. Les états expiré/rendu/sans jeton restent informatifs ; restitution volontaire indisponible conformément à #4048.

Le module historique `test_core_0923.py` est absent du tree relu. Le plan de 0.9.23 excluait le choix visuel multi-profils, l'acquisition entièrement perdue, les autres détails d'Artifact sans ledger et la recette GTK réelle : ne pas leur attribuer une validation implicite.

## Rationalisation Core 0.9.25–0.9.29 — capitalisation du 28 novembre 2026

Relecture statique des témoins au commit `35fcbd1cbe6726ff4bcc72b8335398416e26f795`, sans nouvelle exécution des suites. Les index historiques ne valent pas validation fonctionnelle actuelle.

### 0.9.25 (#124)

- OPEN-C01/DETAIL-C05 ([#130](https://github.com/pzia/cifereca/pull/130), [tests](../tests/test_core0925_open_destinations.py)) : fichier exact et identité revérifiés au clic, erreur visible, playlist de bundle, placement unique attesté pour le détail distant/prêt, matérialisation locale entre identités sans acquisition implicite.
- DETAIL-C06 (#126) est obsolète : DETAIL-C07 remplace la barre basse par une barre haute fixe sur une ligne à défilement horizontal. Ne pas restaurer l'ancien layout.
- COVER-C01 ([#131](https://github.com/pzia/cifereca/pull/131), [tests](../tests/test_epub_cover_contract.py)) : couvertures EPUB2/3 déclarées, OPF borné, cache par contenu réutilisé après déplacement/reconstruit si corrompu, original intact. La [résolution commune](../tests/test_core_493_cover_provenance.py) priorise la couverture embarquée dans Bibliothèque/Récents.
- META-C03/DESC-C01 ([#132](https://github.com/pzia/cifereca/pull/132), [tests](../tests/test_core0925_local_metadata.py)) : notice OPF locale et attributs d'origine, titre, contributeurs, identifiants, éditeur, dates, langues, sujets, droits, série et description HTML normalisée en texte ; extraction sans réseau. [Scan](../tests/test_catalog_083.py) : réutilisation du hash si taille/mtime inchangées. Ne pas confondre provenance catalogue et EPUB, ni déduire une preuve exhaustive de non-reparse après déplacement du seul test META-C03.

Hors périmètre historique : COVER-C02/P02, STORE-P01, divergences PDF exhaustives et prêts ADEPT suspendus à cette date.

### 0.9.27 (#133, #150)

- DETAIL-C07 ([#135](https://github.com/pzia/cifereca/pull/135), [tests](../tests/test_core0927_detail_layout.py)) : barre fixe haute, ligne à scroll horizontal, corps vertical extensible et scrollable borné par le parent ; groupes ajoutés sans mutation de la barre. #151 est un doublon de traçabilité.
- GTK-C08 ([#145](https://github.com/pzia/cifereca/pull/145), [tests](../tests/test_core0927_gtk_lifecycle.py)) et [livraison de tâches #158](../tests/test_core0927_task_delivery.py) : conserver le modèle StringList, décrémenter le compteur, ignorer widgets après fermeture, ne pas réactiver le contrôle détaché tout en livrant le succès si la fenêtre vit. Ne pas déclarer résolus les warnings GTK/Foliate/MESA sans recette réelle.
- [Refresh prêt #154](../tests/test_core0927_loan_refresh.py) : instance fraîche sur identité complète du prêt, disparition → détail vidé ; résultat vide ne prouve pas l'absence de prêts fournisseur.
- [LIST-C03/C04/FILTER-C03 #144](../tests/test_core0927_list_filter_contract.py) : QueryState immuable, filtrage local sans réseau, facettes et contexte conservés, état restauré après reconstruction. Le plan prévisionnel #136 a été remplacé par l'index #150 fondé sur le vrai fichier historique ; #138–#142 sont not_planned, pas des livraisons (genre/description, détail, brokers comptes, sync/pages, résolution widgets).
- ADE-C10/C11 : réconciliation d'inventaire et liaison manuelle couvertes par les témoins 0.9.21/0.9.23 ci-dessus ; les anciens critères de restitution ne prescrivent aucune réactivation après #4048.

### 0.9.29 (#146)

- STATS-C01 ([#149](https://github.com/pzia/cifereca/pull/149), [tests](../tests/test_core0929_statistics.py)) : projection persistée par identité, livres distincts/emplacements, prêts connus et droits observés, inconnu distinct de zéro, répartitions source/format/couverture, sans réseau ; Dashboard/CLI partagent la projection. [Scope strict](../tests/test_core_p0_5851_statistics_identity_scope.py) : identité explicite.
- COVER-C03 ([#156](https://github.com/pzia/cifereca/pull/156), [tests](../tests/test_core0929_cover_c03.py)) : guide XHTML/SVG et spine à image unique, méthode/provenance, cache par contenu et schéma versionné ; réinspection forcée Bibliothèque/CLI `--refresh-covers`, sans téléchargement ni modification du livre, sans rasterisation de première page. Recette EPUB/GTK réelle hors périmètre.
- OBS-C29 ([#161](https://github.com/pzia/cifereca/pull/161), [tests](../tests/test_core0929_observations.py)) : fusion persistée multi-comptes, cache ancien ne remplace pas l'observation fournisseur fraîche, amorçage d'un compte manquant, lecture sans réseau, mise à jour atomique et récupération du payload legacy ; la relecture ne démontre pas à elle seule une campagne exhaustive d'injection de panne.

## État historique 0.9.31–0.9.33 à requalifier

Le compte rendu [#112](https://github.com/pzia/cifereca/issues/112) du 7 octobre 2026 indique le triage 0.9.23–0.9.33 terminé (invariants migrés, déjà couverts ou obsolètes, sans restauration en bloc), à l'exception des invariants ADEPT alors suspendus par l'arbitrage du 4 octobre. Ce bilan historique ne prouve pas la couverture actuelle. Les sources suivantes restent à requalifier dans la documentation selon les contrats actifs, sans restauration transitive :

- `test_core_0931.py` ;
- `test_core_0933.py`.

Ces fichiers historiques sont absents du tree de référence. Classer chaque invariant selon son propriétaire actuel ou son obsolescence.

## Validation minimale d'une migration de test

Pour chaque invariant historique retenu :

1. identifier le comportement encore supporté ;
2. choisir le propriétaire actuel (Core ou plugin) ;
3. écrire/restaurer le test dans le bon emplacement sans importer le test historique ;
4. lancer le test ciblé ;
5. lancer la suite Core ou plugin pertinente ;
6. documenter explicitement les assertions abandonnées comme historiques si elles ne correspondent plus à l'architecture actuelle.
