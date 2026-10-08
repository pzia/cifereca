# Constellations

Plugin prospectif de navigation relationnelle entre livres.

## Graphe explicable

Le moteur consomme des références de livre structurelles compatibles avec le `BookRef` public et leurs métadonnées. Il déduplique d'abord par identifiant attesté (ISBN, provider + identifiant source), puis seulement par titre + auteur lorsqu'ils sont tous deux présents.

Chaque relation conserve ses raisons et son poids : auteur, série, sujet, période, langue, longueur, format, humeur ou proximité éditoriale. Aucun score opaque n'est utilisé.

L'état d'accès reste explicite : `owned`, `borrowable`, `free` ou `external`.

## Modes

- **Confort** : privilégie les voisins proches.
- **Explorateur** : augmente la distance contrôlée avec le curseur proche ↔ surprenant.
- **Projet** : conserve un sujet partagé et favorise une progression lorsque le niveau de difficulté est attesté.
- **Humeur** : utilise uniquement des tags humeur/tonalité qualifiés ; les attributs marqués comme inférés restent signalés comme tels.

## Promenade

`promenade()` construit une chaîne de 4 à 8 livres entre deux points et propose les variantes `short`, `balanced` et `surprising`. Chaque transition expose ses raisons. Les répétitions d'auteur ou de série sont pénalisées. `replace_step()` permet de remplacer une étape intermédiaire sans recalculer les autres étapes.

## Cache

`ConstellationService` isole son cache par identité. Un `freshness_token` différent reconstruit le graphe ; `invalidate()` permet une invalidation explicite lors d'une notification de fraîcheur.

## Périmètre

Ce groupe traite #701, #702 et #703. La vue spatiale GTK #704 reste séparée et dépend des contrats publics Core suivis dans #705. Le plugin n'importe aucun module privé `cifereca.*` et ne déclenche aucune acquisition implicite.
