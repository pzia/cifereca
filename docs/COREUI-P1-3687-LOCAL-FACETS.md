# #3687 — Disponibilité des facettes dans les filtres locaux

Le composant partagé `LocalQueryControls` réutilise la barre de Recherche pour les résultats déjà chargés (Bibliothèque / Recommandations). **Source** et **Langue** ne sont affichés que si les choix de la projection courante sont connus ; lorsque la liste des choix est vide, aucun menu « Tous » sans valeur n’est proposé. Les filtres **Format** et **Type** suivent déjà cette règle via `set_document_choices`.

`set_choices(sources=..., languages=...)` conserve les choix sélectionnés qui restent disponibles, et supprime les valeurs devenues indisponibles ; son actualisation programmatique ne lance pas de recherche ni de recalcul. L’absence de choix ne prouve pas l’absence de documents ou de droits. Les métadonnées `unknown` ne sont pas inventées ; le filtre reste local et réversible.

Ce comportement est limité à `LocalQueryControls` : la barre globale `QueryControls` de Recherche garde ses sources et langues visibles pour préparer une nouvelle recherche. La recette GTK réelle `search-filter-p0` vérifie les transitions initiale → disponible → rafraîchie → vide → disponible, la survie des sélections valides et l’absence de notification/requête pendant un rafraîchissement programmatique. La recette principale Recherche reste inchangée.
