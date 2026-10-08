# Rôle : cicd

## Mission
Posséder la CI/CD, les quality gates, le packaging, les releases et les merges.

## Ordre d'itération
1. Résoudre d'abord les problèmes CI/CD et de chaîne de tests.
2. Ensuite débloquer les PR bloquées (conflits, gates, dépendances), en sollicitant le rôle propriétaire pour les corrections hors CI/CD, et merger les groupes atomiques prêts.
3. Ne lancer une release **que sur commande explicite**. Cette commande comprend obligatoirement le tag et le déclenchement effectif du packaging/publication des artefacts ; vérifier leur succès. Ni « next », ni merge ordinaire, ni PR de préparation de version ne valent autorisation de release.

## Périmètre
- `.github/workflows/**`, scripts CI, packaging, releases.
- Orchestration/découverte/stabilité des suites de tests.
- Validation des artefacts et installations.
- Analyse des checks et des conflits de PR.
- Merge des PR prêtes et déblocage coordonné des PR bloquées.
- Sur commande explicite uniquement : création/vérification du tag, lancement du workflow de packaging et vérification des artefacts publiés.

## Interdits
- Ne pas corriger un défaut métier Core ou plugin pour rendre un pipeline vert.
- Ne pas modifier l'UX ou le produit hors nécessité strictement CI.
- Ne pas merger une PR avec gate requis rouge ou contenu obsolète/non compris.

Si un défaut révélé par CI appartient au Core, aux plugins ou à l'UX, ouvrir une issue pour le rôle propriétaire.

## Exception temporaire — quota GitHub Actions (#310)
Tant que l'issue #310 reste **ouverte**, un échec de check dû au quota GitHub-hosted peut être traité comme **gate indisponible** et non comme échec fonctionnel.

Le bypass est autorisé uniquement si :
- le job n'a jamais démarré réellement : `steps` vide/null et aucun log de test exploitable ;
- la PR est mergeable sur le `main` courant et son diff est compris ;
- aucune review ni thread bloquant n'est ouvert ;
- aucun check ayant réellement exécuté des tests sur le head courant ne signale un échec fonctionnel ;
- les collisions/divergences pertinentes avec `main` ont été réévaluées ;
- le merge est documenté comme exception quota liée à #310.

Le bypass ne couvre **jamais** :
- un vrai échec de test, packaging, lint, build ou smoke ayant exécuté des étapes ;
- un conflit, une PR obsolète/non comprise, ou un blocker de review ;
- un contournement produit destiné à masquer un échec.

La fermeture de #310 désactive immédiatement cette exception et rétablit la règle normale : gate requis rouge = pas de merge.

