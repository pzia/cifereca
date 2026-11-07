# P1 #4761 — filtres locaux invalides : fermeture sûre du rendu

La barre commune de filtres est un composant Core, mais le plugin Recommandations doit gérer une page restaurée ou un ancien host retournant des données malformées. Une sélection Source contenant des listes/objets, une langue non textuelle, un texte de longueur hors contrat ou un getter défaillant ne doivent **ni** provoquer un TypeError pendant le rendu **ni** convertir la sélection en « Tous ». Cette dernière erreur pourrait réexposer des recommandations et des actions de notice d'une source exclue.

La méthode `UxRecommendationsView._local_filter_state` projette seulement un texte imprimable et borné et des listes/tuples de codes publics validés, sans repr ni coercition d'objet. Sur projection invalide, `_matches` ne sélectionne aucun résultat ; le compteur indique « Filtres locaux indisponibles » sans refléter des données privées. La liste vide révoque la sélection/détail précédent et ses actions, via le contrat existant de `_render`. Une exception d'adaptateur de format ou de sélection de champ bloque également la correspondance, sans aucune recherche supplémentaire.

Le garde indépendant `_selected_catalogue_sources` conserve sa validation stricte des routes d'actions exactes (#4664) : ce correctif ne crée aucune possibilité de télécharger, emprunter ou acheter.

Tests headless : `test_p1_4761_malformed_local_filter.py` vérifie listes non hashables, objets à __str__ interdit, sources/langues malformées, getter défaillant, champ invalide, cas positif et invalidation d'une sélection active. La recette GTK Recommandations de la PR valide le socle partagé et la sélection normale.
