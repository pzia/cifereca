# P1 #4682 — suppression du titre visuel « Essentiel » dans le bloc supérieur

Le choix UX du ticket pilote #4682 est explicite : ne plus afficher le mot **« Essentiel »** au-dessus des métadonnées attenantes à la couverture, sans perdre les champs ni leurs actions.

La modification est strictement ciblée dans le renderer partagé `DetailPanel._group` : le groupe `Essentiel` conserve sa clé sémantique interne, sa grille, ses lignes et ses actions de copie/recherche ; seul le titre visuel de `Adw.PreferencesGroup` est vide. Les titres des autres groupes (`Notice`, `Détail`, `Bibliothèques et accès`, etc.) restent affichés.

Les contrats existants sont inchangés : projection `UiBookDetailPresentation`, extraction de `Essentiel` dans `show_presentation`, ordre couverture → champs essentiels → résumé → autres sections, filtrage des données privées, reflow et focus des actions. Un test de structure hors affichage graphique vérifie explicitement l'absence du titre visuel et la conservation de ces chemins ; les suites UI/Core existantes servent de non-régression.

**Limites :** cette tranche ne démontre ni la densité réelle des champs, ni la conformité globale au mockup #566. La validation GTK sur largeurs réelles du volet, la hiérarchie des actions et les cartes de provenance par profil restent dans #4682. Aucune donnée personnelle ni capture propriétaire n'est publiée.
