# P0 #5376 — preuve de collection pytest sur GitHub Actions

## Déclenchement

Le workflow `.github/workflows/test-collection-evidence.yml` est **manuel uniquement** : Actions → **P0 test collection evidence** → **Run workflow**, choisir `core`, `provider-paris`, `provider-kobo` ou `provider-numilog`.

Il ne s'exécute pas sur les PR ou les pushes. Il utilise un runner Ubuntu éphémère, des permissions `contents: read` et un checkout sans identifiants persistants. **Ne jamais ajouter de secrets fournisseur** à ce workflow : `pytest --collect-only` importe des modules de test et peut exécuter des hooks d'initialisation, même si aucune fonction de test n'est exécutée.

## Résultat et limites

Une collecte réussie produit un artefact `pytest-collection-<suite>-<SHA>.zip` conservé 30 jours, contenant `inventory.json`. La vérification impose le SHA exact du checkout, la suite choisie, une collection non vide, un code retour nul et une matrice dont la somme correspond au nombre d'items. Toute erreur bloque la publication de l'artefact.

Le JSON comprend les `nodeid` paramétrés, les classifications attestées par les manifestes, les catégories `unclassified` et les marqueurs `skip`/`xfail`. Il **ne mesure ni exécution, ni succès, ni échec, ni couverture métier**. Le nombre de cas collectés peut varier selon les dépendances et les conditions d'import : comparer seulement des rapports de SHA et d'environnement explicitement identifiés.

Ce workflow ne modifie pas les contrôles CI requis ni les conditions de fusion. Les résultats ne sont pas une attestation de compatibilité réelle des plugins, d'authentification fournisseur ou de sûreté DRM. En cas d'échec, examiner le journal de collection et corriger l'environnement ou le test ; ne pas transformer une collecte partielle en mesure de référence.
