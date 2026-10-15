# Core P0 #981 — éviter les rafraîchissements déjà obsolètes en file

Le rafraîchissement des pages **Bibliothèque** et **Récents** conserve
un compteur de génération par requête et le contexte d'identité.
La vérification historique était exécutée **après** le travail fournisseur :
une tâche en attente de worker pouvait continuer à lire un ancien profil
alors que le résultat serait ensuite rejeté en GTK.

## Contrat apporté

Avant tout accès à `library_entries`, `recent_acquisitions` ou
`catalog_scan(force_covers=True)`, les fonctions `work()` existantes
vérifient que :
- la fenêtre existe encore ;
- aucune demande plus récente du même type ne l'a supplantée ;
- l'identité sélectionnée **et sa génération** correspondent à celles
  capturées à la soumission.

Lorsqu'une tâche devient obsolète avant son démarrage, elle retourne
une liste vide sans consulter le fournisseur ni le disque. Le callback
historique de fin s'occupe de réémettre **au maximum une** demande simple
pour l'identité actuelle. Une demande utilisateur de scan devenue périmée
ne se transforme jamais en scan automatique du nouveau profil.

## État vide déjà chargé ≠ cache jamais initialisé

Après un chargement **réussi**, les adaptateurs mémorisent uniquement la
génération numérique de l'identité : `_library_loaded_generation` et
`_recent_loaded_generation`. Ces drapeaux ne contiennent aucun identifiant
privé. La route GTK ne lance plus de rafraîchissement paresseux sur une page
vide lorsque sa génération est déjà connue comme chargée. Le prochain
changement d'identité modifie le compteur et invalide automatiquement ces
marqueurs ; une erreur ou un travail obsolète ne les met jamais à jour.

Un clic utilisateur sur l'action Actualiser n'est jamais supprimé par ce
mécanisme : la garde ne s'applique qu'aux rafraîchissements paresseux de
navigation. La vue vide reste rendue normalement depuis le cache.

Le mécanisme n'annule **pas** une opération déjà entrée dans le service :
le changement de profil peut encore intervenir après le test d'entrée.
Cela reste protégé par le rejet du résultat obsolète et par les verrous
existants. La suppression des tâches identiques de `TaskRunner` ainsi que
les synchronisations de providers sont inchangées.

## Reproductibilité / limites

Tests headless dans
`tests/test_core_p0_981_stale_queued_library_work.py` :
profil A→B, A→B→A, demande manuelle postérieure,
couvertures et scan de disque différés, fermeture et demande courante.

Aucune nouvelle tâche, lecture réseau, cache global, réconciliation ni
identifiant privé dans les traces. Ce lot réduit les accès devenus inutiles ;
il **ne mesure ni** une amélioration en millisecondes ni une latence frame.
Les performances réelles et la recette GTK multi-identités restent dans
#981 / #1741.
