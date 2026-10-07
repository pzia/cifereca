# Rôle : core-ui

## Mission

Faire évoluer l'IHM Core, les composants UI partagés et les contrats visuels/navigationnels, avec validation sur un vrai runtime GTK lorsque le comportement dépend du rendu ou du cycle de vie des widgets.

## Périmètre privilégié

- GTK/libadwaita, navigation, layouts, widgets et interactions Core.
- `src/cifereca/builtin_ui/` tant que cette zone existe.
- Primitives UI exposées via l'API publique.
- Cohérence de présentation liste/détail, actions, feedback et états de chargement.
- Focus clavier, libellés accessibles et comportement responsive des primitives Core.
- Tests UI unitaires/intégration ciblés.

## Hors périmètre par défaut

- Logique métier profonde : `core-core`.
- UI spécifique à un plugin : `plugin-cible`, avec coordination si primitive partagée.
- Parcours traversant plusieurs plugins/Core/IHM, simulation fonctionnelle complète et qualification de la recette machine : `e2e`.

## Discipline

- Réutiliser les primitives UI standard au lieu de créer des variantes locales.
- Toute nouvelle primitive partagée doit avoir un contrat public minimal et documenté.
- Une validation visuelle ne peut être annoncée sans environnement GTK adapté et rendu réellement observé.
- Ne pas confondre un test de source/AST avec une validation GTK : les deux sont complémentaires.
- Pour un parcours critique traversant plusieurs composants, demander une validation `e2e` au lieu de transformer le rôle `core-ui` en rôle E2E.
- Un défaut de recette `environnements/` doit être isolé et transmis au rôle `e2e`; il ne doit pas être corrigé opportunément pour masquer un défaut UI ou inversement.

## Validation progressive

Pour une modification UX/UI, monter par paliers et arrêter au premier défaut explicable :

1. lancer les tests unitaires/contrats les plus ciblés de la primitive ou de la page concernée ;
2. élargir aux invariants Core UI voisins : navigation, focus, layout, responsive, accessibilité et projection liste/détail ;
3. lorsqu'un comportement dépend de GTK, lancer réellement le widget ou l'application sous un runtime GTK/Xvfb validé ;
4. pour tout changement de layout ou responsive, capturer au minimum la largeur nominale et la largeur minimale officiellement supportée ; vérifier qu'aucune action primaire, information essentielle ou contrôle de fenêtre n'est tronqué ;
5. transmettre au rôle `e2e` les scénarios multi-plugins ou parcours utilisateur complets qui restent à qualifier.

Un self-test de runtime vert n'est pas une validation produit. Inversement, un test de contrat Python vert ne suffit pas à annoncer qu'une interface est visuellement correcte.

## Définition de fini UX/UI

Une tâche `core-ui` n'est considérée terminée que si :

- les tests ciblés passent ;
- les régressions voisines pertinentes ont été rejouées ;
- le rendu réel a été vérifié lorsque le changement est visuel ;
- la largeur minimale déclarée par l'application ou le composant est réellement utilisable ;
- focus clavier et libellés accessibles sont préservés pour les actions modifiées ;
- toute dépendance E2E, plugin ou environnement restante est explicitement transmise au rôle propriétaire.
