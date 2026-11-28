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

## Rationalisation Core 0.9.31–0.9.33 — capitalisation pilote XL #8411

Relecture statique des témoins sur `6e2720af6c84149c8e5d7dee792321d0b2933630`. Le triage historique #112 ne vaut pas preuve fonctionnelle actuelle ; ne pas restaurer les modules historiques `test_core_0931.py` et `test_core_0933.py` en bloc.

### 0.9.31 (#160, #162)

- COVER-C31 ([#163](https://github.com/pzia/cifereca/pull/163), [tests](../tests/test_core0931_cover_preview.py)) : pool dédié deux workers, taille de liste adaptée au facteur d'échelle, cache par source/empreinte/taille, pas d'agrandissement et ratio conservé, jobs dédupliqués et plafond 64, dimensions 2048, 24 Mio et 50 millions de pixels bornés, PNG atomique, fallback comptabilisé vers original, détail conservant l'original. Les assertions textuelles ne prouvent pas le comportement réel de GdkPixbuf/GTK.
- ADE-C31 : suspendu par arbitrage du 4 octobre 2026 ; reclassé sans duplication dans [retour](../tests/test_adept_return.py) et [liaison manuelle](../tests/test_adept_manual_binding.py) : succès historique uniquement après inventaire returned=true/token_present=false, conflit ou verification_failed non annoncé comme succès, artefact local conservé, aucune restitution implicite au rattachement manuel. Pas de réactivation après #4048. DETAIL/LIST/FILTER relèvent des migrations précédentes.

### 0.9.33 (#165–#168)

- SourcePlacement ([#179](https://github.com/pzia/cifereca/pull/179), [tests](../tests/test_core0933_source_placement.py)) : acquisition complète attestée pour identité/provider/credential/item exacts ; aucun rapprochement par titre seul ; un seul placement local présent, fichier absent ou plusieurs copies → pas de liaison/action locale ; lecture sans sync/acquisition. Témoins structurels.
- PublicLink ([#180](https://github.com/pzia/cifereca/pull/180), [tests](../tests/test_core0933_public_links.py)) : préférer friendly_url publique, refuser URLs de livraison/signées, file/javascript/userinfo/loan/token/AWS/acsm/port non standard ; notice Paris relative contre site configuré, fallback homepage publique déclarée ; origine inconnue sans notice inventée.
- RefreshAndNavigation ([#184](https://github.com/pzia/cifereca/pull/184), [tests](../tests/test_core0933_refresh_navigation.py)) : refresh limité aux sessions de prêt de l'identité, ADEPT inventaire en lecture, pas de sync store/acquisition ; résultats obsolètes ignorés, route/identité/providers validés, UiIdentityViewHost optionnel sans élargissement de UiIdentityHost. Assertions structurelles, pas recette de course réelle.
- Index #165 : six migrations non-ADEPT livrées historiquement ; autres témoins présents : [DetailsAndActions #186](../tests/test_core0933_details_actions.py) (actions dédupliquées, deux colonnes, budget largeur, remplacement des facettes inline), [ResponsiveWidgets #188](../tests/test_core0933_responsive_widgets.py) (objets boutons conservés, label accessible, ordre, restauration, sensibilité miroir), [DeveloperExamples #191](../tests/test_core0933_developer_examples.py) (manifestes provider/page, capacités et origine publique exactes, page sans import GTK). ADEPT Conflicts reste hors de ce périmètre, pas de reprise implicite.

La relecture ne constitue ni une nouvelle exécution des suites ni une recette utilisateur réelle.

## CI, migrations Core et socle E2E — capitalisation XL #8418

Relecture statique des 20 issues et de leurs commentaires, manifeste `fd175c4944366101f0b0ea0c37686e659a79b838939eae1fd37c1c8badf1e568`, témoins sur `41d29f82a58ed935ae97a29c34b4e3f078ea8ee5`. Les conclusions historiques de CI ne constituent pas une nouvelle réception au HEAD courant.

### Gates et livraison (#169, #170, #172, #174, #175, #177, #183)

- #169 : gate Core historique sur PR et main ; aujourd'hui [PR CI](../.github/workflows/pr-ci.yml) sélectionne les suites Core et plugins, [Test Core](../.github/workflows/core-tests.yml) couvre push main et déclenchement manuel, avec sélection et repli exhaustif. Ne pas confondre contrat historique exhaustif et routage actuel.
- #170/#172 : [Validate installed Core](../.github/workflows/core-install.yml) construit le wheel sans isolation de build, matrice 3.11/3.12/3.13 ; [smoke installé](../scripts/ci-smoke-installed-wheel.sh) dans un venv et hors arbre source, vérifie import, SHA attendu et CLI/entrypoints. La matrice porte ici sur l'installation, pas sur l'intégralité des tests Core.
- #174 ([#185](https://github.com/pzia/cifereca/pull/185)) : permissions, Python explicite, timeouts, annulation des runs obsolètes et cache lorsque pertinent ; historique plugins/packaging/dataset/wheel verts, Core encore rouge sur six assertions préexistantes. Les workflows de publication ont une politique de concurrence distincte (pas d'annulation en cours) ; pas de garantie de pinning SHA généralisé.
- #175/#183 : [release](../.github/workflows/release.yml) sur tag/commande explicite, vérification commit intégré et métadonnées/version, validations avant publication, wheel/archives, ensemble exact d'artefacts, SHA-256 et signatures, draft avec relecture des assets téléchargés avant publication, distinction prerelease/stable. Aucune release déclenchée pour cette capitalisation, ni preuve de publication actuelle.
- #177 : [E2E Smoke](../.github/workflows/e2e-smoke.yml) séparé de Core, sélection sur PR, replay intégré sur main selon chemins, runtime GTK/headless et scénarios synthétiques ; pas de validation fournisseur réel induite.

### Migrations Core et baseline (#171, #173, #176, #193, #194, #200, #207, #211, #221)

- #171/#173/#176 : migrations de #165 déjà capitalisées ci-dessus ; [DetailsAndActions #186](../tests/test_core0933_details_actions.py) (actions dédupliquées, colonnes et budget, facettes source et rebinding), [ResponsiveWidgets #188](../tests/test_core0933_responsive_widgets.py) (objets boutons, accessibilité, ordre, restauration, sensibilité miroir), [DeveloperExamples #191](../tests/test_core0933_developer_examples.py) (manifeste, capacité de recherche, provenance et enregistrement sans GTK). Ne pas restaurer un module historique monolithique.
- #193 : 14 échecs historiques (617 pass, 2 skipped), [#206](https://github.com/pzia/cifereca/pull/206) corrige les faux positifs de tests, découple PluginManager du transport et préfère provenance de symbole à identité d'objet. Témoins actuels : [builtin UI](../tests/test_builtin_ui_rationalization.py), [Step B](../tests/test_cifereca_step_b.py), [dépendances Python](../tests/test_core037_python_dependencies.py), [placement](../tests/test_core0933_source_placement.py). Le DONE historique n'est pas une nouvelle mesure de baseline.
- #194 : [#205](https://github.com/pzia/cifereca/pull/205), #195 obsolète après migration ; [résilience Paris](../src/plugins/provider-paris/tests/test_legacy_client_resilience.py) utilise le stub Diagnostics.add(category, operation, status, duration_ms, detail, **kwargs), avec cas login, résultats physiques conservés, retry réduit et arrêt après deuxième erreur protocole. #200 : imports locaux de [recommendation_service](../src/plugins/recommendations/tests/test_recommendation_service.py) et module présent dans le package ; [runner plugins](../scripts/ci-run-plugin-tests.sh) exécute chaque package dans son répertoire via python -m pytest -q, propage l'échec. Pas de preuve de collecte réexécutée.
- #207 : [Kobo boundary](../tests/test_kobo_boundary_rationalization.py), chemin unique src/plugins/provider-kobo/cifereca_kobo_vault.py, AST des classes KoboVaultError/KoboVaultClient, absence du pont Core ; pas de fallback legacy. #211 : [statistics](../tests/test_core0929_statistics.py), comparaison AST sur cmd et constante statistics, appel identity_statistics ; indépendant des quotes d'ast.unparse.
- #221 ([#224](https://github.com/pzia/cifereca/pull/224)) : six assertions corrigées historiquement sans xfail/skip ni modification de production ; témoins spécialisés actuels [ADEPT attach initial faux](../tests/test_adept_manual_binding.py), [GTK lifecycle](../tests/test_core0927_gtk_lifecycle.py), [QueryState](../tests/test_core0927_list_filter_contract.py), [guide/first_spine](../tests/test_core0929_cover_c03.py), statistics et DetailsAndActions ci-dessus. Les assertions structurelles ne prouvent pas à elles seules le comportement GTK ou le fallback EPUB réel.

### Socle E2E (#196–#199)

- #196 : [runner](../e2e/run.py) et [guide](../e2e/README.md), ciblage scénario ou plugin, startup et core-navigation ; [contrat](../tests/test_e2e_runner_contract.py) vérifie la sélection. Le LOCK historique n'est pas un DONE ; pas d'attestation exhaustive d'absence de sleeps ni de déterminisme de tous les parcours.
- #197 ([#228](https://github.com/pzia/cifereca/pull/228)) : runner avec artefacts par scénario, stdout/stderr expurgés, trace JSONL horodatée, capture sur échec selon le contrat historique ; ne pas déduire une garantie universelle de masquage de tout secret ni la présence effective d'une capture dans chaque run.
- #198 ([#227](https://github.com/pzia/cifereca/pull/227), [#231](https://github.com/pzia/cifereca/pull/231)) : fixtures versionnées startup et autres, HOME/XDG temporaire, bibliothèque vide et aucun compte réel pour startup ; [tests simulation](../simulation/tests/test_e2e_fixtures.py) vérifient état explicite empty/smoke, digest reproductible après contamination, chemins XDG isolés, cleanup y compris exception. Pas de profil réel nécessaire dans ces témoins.
- #199 ([#230](https://github.com/pzia/cifereca/pull/230)) : E2E Smoke distinct, résumé/artefacts de debug et rétention 7 jours dans le DONE historique ; workflow actuel avec sélection et politique de replay. Les preuves exactes d'une recette dépendent de son run et de ses artefacts, non de l'existence du YAML.

Aucune nouvelle exécution des suites métier, collecte plugin, recette GTK, benchmark de temps ou publication de release n'est revendiquée par cette relecture.

## Validation minimale d'une migration de test

Pour chaque invariant historique retenu :

1. identifier le comportement encore supporté ;
2. choisir le propriétaire actuel (Core ou plugin) ;
3. écrire/restaurer le test dans le bon emplacement sans importer le test historique ;
4. lancer le test ciblé ;
5. lancer la suite Core ou plugin pertinente ;
6. documenter explicitement les assertions abandonnées comme historiques si elles ne correspondent plus à l'architecture actuelle.
