# #4761 — Compteur et recalcul avec filtres locaux persistants

Un recalcul de Recommandations remplace les résultats classés tout en conservant les facettes d’affichage déjà choisies. Après cette publication, le compteur doit refléter **les résultats visibles**, non annoncer à tort le total brut si une recherche texte, un format, un support, une source, une langue ou un seuil d’affinité réduit la vue.

`_base_complete` recalcule donc le résumé local après le rendu. En l’absence de filtre actif et si tous les résultats sont visibles, `preserve_unfiltered=True` conserve la synthèse initiale détaillée (livres analysés, nombre de signaux, recherches partiellement en erreur), plutôt que la remplacer par un simple total. Les résultats vides et les erreurs gardent leurs explications spécifiques ; le diagnostic cache/réseau est ajouté ensuite par le chemin existant.

Le test déterministe `test_p1_4761_recalculation_local_count.py` couvre vue intégrale, recherche texte maintenue, format, seuil d’affinité et résultat vide. L’E2E GTK de Recommandations reste une régression obligatoire. Aucune nouvelle requête fournisseur, permission ni reclassification du classement n’est déclenchée par les filtres d’affichage.
