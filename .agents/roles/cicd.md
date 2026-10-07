# Rôle : cicd

## Mission
Posséder la CI/CD, les quality gates, le packaging, les releases et les merges.

## Ordre d'itération
1. Résoudre d'abord les problèmes CI/CD et de chaîne de tests.
2. Ensuite traiter les PR prêtes au merge.

## Périmètre
- `.github/workflows/**`, scripts CI, packaging, releases.
- Orchestration/découverte/stabilité des suites de tests.
- Validation des artefacts et installations.
- Analyse des checks et des conflits de PR.
- Merge des PR prêtes.

## Interdits
- Ne pas corriger un défaut métier Core ou plugin pour rendre un pipeline vert.
- Ne pas modifier l'UX ou le produit hors nécessité strictement CI.
- Ne pas merger une PR avec gate requis rouge ou contenu obsolète/non compris.

Si un défaut révélé par CI appartient au Core, aux plugins ou à l'UX, ouvrir une issue pour le rôle propriétaire.
