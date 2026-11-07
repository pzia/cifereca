# CoreUI P1 #3687 — Remise à zéro des filtres Recherche / locaux

La barre partagée `QueryControls` présente une action clavier explicite
**Effacer les filtres**, à côté du champ texte. Elle appelle `reset_filters()`
et émet au maximum **une** notification de changement d'état utilisateur,
sans recopier le moteur de filtre dans les plugins.

## Périmètres distincts

- **Recherche** : retour à l'état initial du composant, notamment
  `scope=digital`, champ général, aucune source/format/langue sélectionnée.
  Le contrôleur Recherche reste seul responsable des réactions à ce changement ;
  aucun appel fournisseur n'est déclenché directement par le widget.
- **Recommandations / autres vues locales** : état initial
  `scope=all`, texte vide et aucune sélection de champ, source, format
  ou langue. La liste déjà chargée est seulement refiltrée ; le bouton ne
  lance pas `Recalculer`, ne modifie ni affinité/diversité, ni les
  paramètres de génération.
- Les choix disponibles sont **conservés** : le reset porte sur la sélection,
  pas sur l'attestation des facettes. Le bouton est désactivé lorsqu'aucun
  filtre n'est actif, y compris après suppression *silencieuse* d'une facette
  périmée pendant une mise à jour provisoire ou un changement de profil.
- Les actions exactes du Détail continuent d'obéir aux gardes Core de
  provenance, identité et droits. L'affichage local ne prouve jamais un droit.

## Régressions

`tests/test_query_controls_batching.py` vérifie la remise à zéro des deux
périmètres, une notification unique pour une sélection composite, le reset
idempotent, le maintien des choix de facettes et le bouton désactivé après
réconciliation programmatique. Exécuter la CI Core et la recette GTK #774
sur HEAD exact avant intégration. Ne pas clore #3687 sur cette seule tranche :
navigation clavier, densité à 1024px et homogénéité des compteurs restent
à vérifier sur la vraie application.
