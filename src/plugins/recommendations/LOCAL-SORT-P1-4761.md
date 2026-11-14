# #4761 — Tri local des recommandations

## Contrat livré

La barre de filtres partagée `LocalQueryControls` propose un choix
**opt-in** `enable_local_sort()`, avec quatre ordres :

- **Ordre recommandé (défaut)** : rang diversifié du moteur, inchangé.
- **Affinité décroissante** : score entier réellement admissible 0–100 ;
  valeur malformée classée en dernier, jamais coercée.
- **Titre A–Z** : texte bibliographique public borné.
- **Auteur A–Z** : auteur(s) source validé(s), sans conversion d'objet fournisseur.

Le tri intervient **après** l'application des filtres locaux, sur les lignes
**déjà calculées et présentes**. Aucun nouvel appel catalogue, aucune
reconstruction du profil, aucun droit, aucune opération fournisseur ou LLM.

Un reset « Effacer les filtres » rétablit les filtres initiaux et
**l'ordre recommandé** en un seul changement de présentation. Le tri local
ne modifie pas `QueryState` : les consommateurs historiques Recherche et
Bibliothèque ne gagnent aucune règle de classement. Les positions valides sont
strictement contrôlées ; un état de sélection GTK invalide ne choisit pas une
valeur par conversion numérique.

Les clés de liste GTK pour les variantes d'une même notice sont allouées
**dans l'ordre moteur avant le tri** : un changement de tri ne peut attribuer
l'ancienne clé sélectionnée à une nouvelle variante. La sélection demeure
soumise aux contrôles de génération et d'identité, et les actions gardent
leurs coordonnées provenance exactes.

## Preuves, limites

Fixtures synthétiques : ordre stable et réversible, scores non numériques,
métadonnées non textuelles, deux variantes au même `candidate_key` plus
un identifiant réel qui entre en collision avec un suffixe, maintien
des clés/coordonnées, reset tri seul et reset tri + filtres simultanés.

Cette tranche ne prouve pas la parité visuelle GTK complète à 1024/1366/1600,
ni une amélioration de pertinence, ni le défaut terrain de vignette BnF.
Les suites CI/E2E doivent confirmer les composants communs sur HEAD exact.
#4761 reste ouvert pour ses autres critères.
