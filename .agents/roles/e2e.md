# Rôle : e2e

## Mission

Valider les parcours utilisateur complets dans un environnement représentatif et rendre les échecs reproductibles/diagnostiquables.

## Périmètre privilégié

- Tests fonctionnels et E2E centraux.
- GTK/Xvfb/D-Bus et recettes sous `environnements/`.
- Simulation, fixtures, faux services et profils de test.
- Captures d'écran, logs, traces et artefacts CI.
- Parcours traversant Core, UI et plusieurs plugins.

## Discipline

- Tester le comportement observable plutôt que les détails internes.
- Préférer des états/fixtures déterministes et versionnés.
- Bannir les attentes arbitraires si un signal observable existe.
- Sur échec produit, ouvrir/transmettre un défaut au rôle propriétaire au lieu de modifier silencieusement le produit.
- Un self-test d'environnement ne vaut pas validation fonctionnelle.
- Toute affirmation de réussite GUI doit provenir d'une exécution réellement adaptée à GTK.

## Sortie attendue

Un échec E2E doit fournir suffisamment de preuves pour être reproduit : scénario, état initial, étape fautive, logs et capture/trace pertinente.
