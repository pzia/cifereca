# P0 #5376 — preuve de collection pytest sur GitHub Actions

## Déclenchement

Le workflow `.github/workflows/test-collection-evidence.yml` est **manuel uniquement** : Actions → **P0 test collection evidence** → **Run workflow**, choisir `core`, `provider-paris`, `provider-kobo` ou `provider-numilog`.

Il ne s'exécute pas sur les PR ou les pushes. Il utilise un runner Ubuntu éphémère, des permissions `contents: read` et un checkout sans identifiants persistants. **Ne jamais ajouter de secrets fournisseur** à ce workflow : `pytest --collect-only` importe des modules de test et peut exécuter des hooks d'initialisation, même si aucune fonction de test n'est exécutée.

## Résultat et limites

Une collecte réussie publie les **totaux vérifiés** dans le résumé de l'exécution GitHub Actions (`GITHUB_STEP_SUMMARY`) : SHA exact, suite, cas collectés et cas `unclassified`. La vérification impose le SHA exact du checkout, la suite choisie, une collection non vide, un code retour nul et une matrice dont la somme correspond au nombre d'items. Toute erreur bloque la publication du résumé. Le JSON détaillé contenant les identifiants de tests reste **local au runner éphémère** : sa diffusion comme artefact public n'est pas autorisée par le contrôle de confidentialité #1143. Aucun upload d'artefact n'est effectué.

Le JSON comprend les `nodeid` paramétrés, les classifications attestées par les manifestes, les catégories `unclassified` et les marqueurs `skip`/`xfail`. Il **ne mesure ni exécution, ni succès, ni échec, ni couverture métier**. Le nombre de cas collectés peut varier selon les dépendances et les conditions d'import : comparer seulement des rapports de SHA et d'environnement explicitement identifiés.

Ce workflow ne modifie pas les contrôles CI requis ni les conditions de fusion. Les résultats ne sont pas une attestation de compatibilité réelle des plugins, d'authentification fournisseur ou de sûreté DRM. En cas d'échec, examiner le journal de collection et corriger l'environnement ou le test ; ne pas transformer une collecte partielle en mesure de référence. Pour archiver une ventilation complète, faire approuver un export agrégé sans identifiants de cas selon la politique #1143, plutôt que publier le JSON brut.
