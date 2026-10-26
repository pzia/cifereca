# UX P1 #1867 — Bibliothèque, Recherche et Synchronisation

## Règles de lecture des données (sans nouvelles permissions)

- Une notice de recherche est un **catalogue**, pas la preuve d'un achat,
  même si un champ `owned=True` est émis par une source publique.
- La projection `remote_owned` en recherche est réservée à l'inventaire
  privé Numilog explicitement identifié (`NUMILOG_LIBRARY`,
  `source_collection=library`, credential non public, `owned is True`,
  et aucune provenance de cache public ancien). **Kobo** conserve le
  contrôle de propriété fondé sur le registre des droits du Core, et non
  le booléen arbitraire d'une notice catalogue.
- `numilog-catalog` affiche « Catalogue public Numilog · aucun achat
  attesté » et ne publie **aucun bouton Télécharger** dans la recherche
  même si une notice anonyme rapporte `downloadable=True`.
- La recherche privée Numilog affichée comme « Mes achats » ne prouve
  **pas** la présence d'un fichier EPUB/ACSM sur disque. Le badge
  « Fichier local disponible » repose toujours sur une acquisition/placement
  vérifiés et propres à l'identité.
- La Bibliothèque affiche pour `remote_owned` des **achats en ligne**
  (Kobo/Numilog) ou un autre droit de possession distant, et pour
  `remote_catalog` une « Notice de catalogue · sans droit attesté ».
  Un prêt `remote_loan_active` et un fichier `local` restent distincts.
- Les fiches Recherche portent la même distinction de portée sans
  déclencher de nouvelle récupération de droits lors du rendu.
- Dans Synchronisation, « Kobo — Catalogue » correspond à la capacité
  de recherche du fournisseur Kobo déjà existant, **sans seconde
  connexion Kobo**, tandis que « Numilog — Catalogue public » reste la
  source anonyme optionnelle. La synchronisation d'inventaire personnelle
  est une opération distincte de cette section de recherche.

## Tests

`tests/test_core_ui_p1_1867_search_library_semantics.py` couvre
les indications de propriété contradictoires, les origines privées
incomplètes, l'ancien cache public, les fiches et les états Bibliothèque.
`tests/test_core_ui_p1_2385_catalogue_section.py` vérifie les
libellés Kobo/Numilog dans le broker de catalogues, sans bouton d'action.
Les scénarios GTK existants restent le contrôle d'intégration native.

**Suite #1867 :** harmonisation des autres sous-vues, vérification de
l'accessibilité des libellés et d'un parcours E2E multi-profils avec
inventaires fournisseur réels. Aucun nouveau POST, aucune acquisition
automatique, aucune mutation ADEPT ou de droits.
