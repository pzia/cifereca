# Simulation fonctionnelle CIFERECA

État documentaire : 11 octobre 2026.

## Source canonique actuelle

**`simulation/` est déjà le banc de simulation fonctionnelle unique.** Aucun déplacement `simulation/ → simulation/` n'est à effectuer, et aucun autre répertoire de simulation concurrent n'est prévu.

Le banc comprend notamment :
- faux services BnF, Kobo, Paris, Numilog, OPDS et fournisseurs jeunesse ;
- données et fixtures déterministes (y compris scénarios négatifs et volumes) ;
- profils `simulation/profiles/*.toml` et états de services isolés ;
- orchestration `simulation/datasetctl.py`, isolation XDG et health checks ;
- tests `simulation/tests/`.

## Responsabilités et frontières

La simulation fournit les protocoles simulés, les profils, les données, la latence, les erreurs reproductibles et les états fournisseurs. Elle ne doit pas embarquer de bootstrap GTK, de dépendances OS, de règles réelles de providers ni de logique métier Core.

La recette machine appartient à `environnements/` (Debian 13 ou ChatGPT Work selon hôte), et les tests unitaires Core à `tests/`.

Exemple pour une recette UX :
1. sélectionner l'environnement hôte via [`environnements/README.md`](../environnements/README.md) ;
2. produire/valider un profil de simulation : `python3 simulation/datasetctl.py rebuild ux-small` puis `summary ux-small --json` ;
3. lancer Cifereca sous Xvfb dans l'XDG isolé du profil ;
4. mesurer le scénario et conserver captures, traces expurgées et résultat ;
5. arrêter les faux services et nettoyer **uniquement les données générées**, pas les fixtures versionnées.

## Rationalisation continue

- Inventorier les fixtures/scénarios peu utilisés avant suppression, en recherchant leurs consommateurs (`tests/`, `e2e/`, scripts, plugins, docs).
- Factoriser les faux services réellement communs **sans retirer leurs cas négatifs**.
- Ne pas lancer de tests GTK dans les workflows CI ordinaires lorsque la recette UX a son cycle séparé.
- Les caches `simulation/.runtime/` restent non versionnés ; la suppression de l'arborescence `simulation/` elle-même n'est pas prévue.

Voir [`simulation/README.md`](../simulation/README.md), [`docs/TEST-ORGANIZATION.md`](TEST-ORGANIZATION.md) et [`environnements/README.md`](../environnements/README.md).
