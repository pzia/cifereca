# Plugins P1 #1599 — matrice de conformité des fiches bibliographiques

État du code de `main` examiné le 15 octobre 2026. Parent : #1596 ;
contrat et matrice CoreUI : `docs/COREUI-P1-1598-DETAIL-MATRIX.md` (#1598).

## Invariant commun

Les trois vues s'appuient déjà sur `host.build_book_master_detail`,
`UiBookDetailPresentation`, `DetailSection`, `DetailField` et
`show_detail`. Le rendu GTK est celui du **DetailPanel du CoreUI**.
Ne pas créer un panneau parallèle, importer `cifereca.gui` privé,
inventer un ISBN, assimiler un rapprochement à une possession ou
déclencher une acquisition depuis une recommandation.

## Matrice par plugin

| Contrat | Jeunesse (`youth_page.py`) | Constellations (`constellations_page.py`) | Recommandations (`recommendations.py`) |
| --- | --- | --- | --- |
| Liste / vide | Cache jeunesse de l'identité active ; détail vide explicite ; pas de collecte sans action | Liste de nœuds bornée, aucun livre central arbitraire ; `empty_detail` sans centre | Liste de candidats classés et `empty_detail` si aucune sélection / ligne périmée |
| Titre et auteurs | Titre de la notice, auteurs distincts en champs `kind=author` | Titre du nœud et champs auteur individuels `kind=author` | Titre du candidat, auteurs multivalués en champs `kind=author` |
| Couverture | `cover_url` uniquement après `public_url` | `cover_url` / `thumbnail_url` de la notice brute, validées par `public_url` | `thumbnail_url` / `cover_url` explicites après `_external_book_url` |
| Métadonnées | Âges attestés par nature/provenance ; édition/ISBN, langue, formats, thèmes, éditeurs, année, collection, résumé et sources si présents | ISBN/language/éditeur/séries/formats/sujets **attestés par cette notice** ; voisinage distinct ; disponibilité sans droit inféré | Affinité/raisons/sources ; formats et publication ; éditions distinctes **seulement avec coordonnées attestées** ; description facultative |
| Liens | `friendly_url` puis `notice_url` sûre, `DocumentLink` ; aucun lien d'acquisition | Notice publique de `node.url` seulement ; `friendly_url` seule sur `main`, repli explicite `notice_url` proposé dans PR #1800 | `friendly_url` puis `notice_url` sûre via action externe explicite ; pas de téléchargement ni URL signée |
| Actions | Ouverture locale via hôte, recherche locale explicite, collecte distante volontaire et contrôlée | Voisinage / historique A→B→C ; `open_book_detail` du centre ou du livre d'origine avec l'identité mémorisée | Recherche **locale** d'un ISBN vérifié et rattaché à une notice source ; pas d'action d'acquisition, droits à vérifier dans Recherche |
| Identité | Cache filtré par identité ; profil disparu non remplacé implicitement, annulation des tâches et invalidation de sélection | Chargement par identité, sélection GTK invalide rejetée, cache borné par profil, résultats tardifs rejetés par génération | Recommandations et recherche d'édition liées au profil ; race de changement d'identité durant un calcul corrigée dans PR #1798 (non fusionnée à cette date) |
| Arrêt / réactivation | Callbacks et sélection révoqués lors des changements ; voir tests de cycle de vie | `dispose` annule les tâches et invalide la génération ; ancienne vue inerte | Annulation et générations de calcul/LLM ; validation GTK multi-profils finale encore à établir |

Les sections métier doivent rester distinctes : **« Âges attestés »**,
**« Provenance / Métadonnées bibliographiques »** et
**« Recommandation / Éditions et sources distinctes »**. Une différence de
section métier n'est pas un défaut de factorisation. Les champs communs
doivent cependant conserver leur sens (titre, auteur, ISBN attesté,
langue, source, couverture et URL publique sûre) sans valeur fabriquée.

## Tests déjà présents, sans surinterprétation

- **Jeunesse** : `test_youth_public_url_boundary_p1.py`,
  `test_youth_profile_reload_p1.py`,
  `test_youth_sticky_missing_identity_p1.py`,
  `test_youth_stale_selection_profile_p1.py` et
  `test_youth_detail_dispose_lifecycle_p1.py`.
- **Constellations** : `test_public_detail_multivalue_p1.py`,
  `test_constellations_attested_bibliography_p1.py`,
  `test_constellations_detail_attested_covers_p1.py`,
  `test_constellations_profile_reload_p1.py`,
  `test_constellations_sticky_missing_identity_p1.py`,
  `test_detail_foreign_identity_seed_p1.py` et
  `test_constellations_disposed_actions_p1.py`.
- **Recommandations** : projections et garde-fous dans
  `src/plugins/recommendations/tests/` ; la correction de course
  identité/calcul et ses quatre scénarios headless sont dans PR #1798.

**Important :** l'existence de ces tests ne prouve ni une recette GTK
end-to-end des trois plugins, ni la conformité sur le HEAD fusionné.

## Écarts / dépendances et ordre de sortie

1. **En cours côté Plugins :** PR #1798 (invalidation immédiate après
   changement d'identité) et PR #1800 (repli Constellations sur
   `notice_url` indépendante et sûre). Ne pas les déclarer livrées
   tant que CI/CD ne les a pas fusionnées.
2. **Côté CoreUI #1598 :** comparer sur HEAD final les champs communs
   Recherche/Bibliothèque avec les trois projections publiques, sans
   réutiliser les détails de disponibilité, prêts ou possession lorsque
   la source plugin ne les atteste pas.
3. **Recette UX/CI/CD :** plugins réels activés, GTK 1366 **et** 1600 ;
   0/1/N livres, homonymes/éditions distinctes, auteurs multiples,
   liens invalides, liste→détail→retour, navigation au clavier/focus,
   scroll, A→B→C→origine, sélection rapide, identité A→B, suppression
   d'un profil, désactivation/réactivation et fermeture pendant tâche.
   Confronter les captures et journaux expurgés aux comportements, pas
   seulement au déclenchement de workflow (PR #1797).
4. **Limite de périmètre :** actions métier Recommandations #1354
   expressément hors #1599 ; aucune mutation de droits, backend LLM,
   scraper externe ou second renderer.

**Décision de clôture :** #1599 reste ouvert tant que les deux corrections,
la comparaison structurelle sur #1598 fusionné et la recette GTK finale
ne sont pas établies ; #1596 attend les clôtures des deux enfants.
