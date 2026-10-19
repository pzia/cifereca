# #1599 P1 — Recommandations : matrice de projection maître/détail

État **du code**, audité le 19 octobre 2026 ; ce document ne certifie ni la
recette GTK ni le contrat transverse CoreUI #1598. Le seul plugin en périmètre
de #1599 est `recommendations` ; Jeunesse retiré (#741 P3) et Constellations
P2 (#700) sont **hors périmètre**.

## Surface publique utilisée

Le plugin appelle exclusivement `host.build_book_master_detail(...)` ;
la liste contient des `UiBookListEntry`, le détail est construit avec
`UiBookDetailPresentation`, `DetailSection`, `DetailField` et
`UiBookDetailAction`. Aucun `DetailPanel` secondaire, import privé
`cifereca.gui` ou mutation d'acquisition n'est nécessaire.

| Champ / emplacement | Provenance acceptée | Projection dans Recommandations | Écart contextuel légitime avec Recherche/Bibliothèque |
| --- | --- | --- | --- |
| Titre — liste et détail | `item.title` chaîne publique validée | Libellé identique, défaut « Sans titre » | N'implique pas une édition achetée |
| Auteurs — sous-titres | `author/authors/creator/creators` directs, `metadata`, `epub_metadata`, chaînes bornées et dédupliquées | Sous-titre joint, champs `Auteur` **indépendants** dans « Catalogue » | Pas de création de contributeur non attesté |
| Couverture | `thumbnail_url` puis `cover_url`, URL HTTP(S) publique validée | Même URL sûre en liste et détail ; sinon absente | Aucun lien signé, local, DRM ou d'acquisition |
| Source(s) | `ranked.sources` (max. 32), libellés depuis `_provider_labels` après validation | Champ `Source` indépendant par source dans « Catalogue », badges de provenance | Les sources agrégées ne prouvent pas les droits |
| Format | `item.format`, scalaire public validé | « Format » seulement si attesté, sans inférer EPUB/PDF achetés | Une offre de catalogue n'est pas un artefact |
| Langues | Champs `language/languages` explicites directs ou metadata, via `normalize_language_tag` | Champs « Langue » indépendants, ordre de première attestation | Pas de langue inférée depuis titre ou provider |
| Publication | `item.published_at`, texte public borné | « Publication » seulement si publié | Pas de date de prêt/d'acquisition calculée |
| Description | `description` puis `summary`, texte public validé | Section « Notice » si disponible | Jamais le corps d'un échec fournisseur |
| Score/affinité | `RankedCandidate.score` issu du moteur ; entier 0..100 attendu | Ligne et détail « Affinité », badges ; valeurs invalides : correctif dédié [#2357](https://github.com/pzia/cifereca/pull/2357) | Score de proximité, **pas** disponibilité/droit |
| Raison(s) | `ranked.reasons`, valeurs textuelles bornées | « Pourquoi » dans « Recommandation », absence explicite si non attesté | Pas de motif éditorial fabriqué |
| Classement | Politique de diversification du moteur | Avertissement « Ordre diversifié » dans « Recommandation » | Ne pas confondre avec un score absolu |
| Éditions sources distinctes | `CatalogNoticeVariant` coordonnées provider/credential/record attestées, max. 16 dans le détail | Section « Éditions et sources distinctes », ISBN-13 seulement si checksum valide | Identifiants de compte/record restent **privés** |
| Reformulation LLM (optionnelle) | Résultat optionnel, tagué `non attesté` et fournisseur/modèle | Section distincte « Reformulation générée — à vérifier » | N'est pas un champ bibliographique ni un droit |

**Ordre de sections** (priorités) : « Recommandation » 10,
« Catalogue » 20, « Reformulation générée — à vérifier » 25 si activée,
« Notice » 30 si disponible, « Éditions et sources distinctes » 35 si
variantes attestées. Le moteur/hôte commun gère la présentation GTK.

## Actions, identité et états

| Interaction | Contrat vérifié dans le plugin | Ne pas interpréter comme |
| --- | --- | --- |
| Clic « Chercher l'édition ISBN … dans Recherche » | Seulement ISBN-13 valide d'une variante avec coordonnées attestées ; `host.search_books(isbn, field='isbn', online=False, identity_id=<identité capturée>)` | Acquisition, recherche distante implicite, preuve de possession |
| Lien « Voir en ligne » | `friendly_url` puis `notice_url` si URL publique HTTP(S), sans query/fragment, segment sensible, port privé ni credential | URL privée, ACSM, téléchargement direct |
| Absence d'ISBN / de source | Pas d'action de recherche ISBN ; conservations éventuelles de la notice publique | Absence d'ouvrage dans tous les catalogues |
| Sélection A→B→A | Révocation des callbacks via générations de rang, de détail, clé et objet classé | Autorisation de réutiliser une action périmée |
| Changement d'identité | Vérification à l'activation du callback ; recherche locale pour la **même** identité | Recherche sur un autre lecteur |
| Filtre vide / ligne périmée | `empty_detail` explicite, révocation des sélections asynchrones/LLM | Détail ou action encore utilisable de la ligne précédente |
| Rang provisoire / LLM tardif | Badge « Provisoire », invalidation des callbacks LLM par génération et sélection | Score définitif ou raison prouvée |

Les disponibilités, exemplaires, prêts, droit `owned`, état de
téléchargement et chemin local relèvent de **Recherche/Bibliothèque et de leurs
propres comptes**. Le plugin Recommandations ne les projette pas et ne doit
pas les reconstruire. La convergence métier des actions appartient à #1354
(hors #1599).

## Vérifications et limites ouvertes

Tests **headless** existants du plugin :

- `test_recommendation_detail_projection_contract_p1.py` :
  titre/auteurs/description/URL, cohérence liste/détail, sélection périmée,
  projection multi-champs et contrôle de texte non fiable.
- `test_recommendation_ranked_provenance_public_p1.py` :
  bornes sur raisons/sources, absence d'injection de données privées.
- `test_recommendation_catalogue_navigation_p1.py` :
  éditions homonymes, ISBN source, action locale, générations et identité.
- `test_recommendation_identity_switch_while_busy_p1.py` :
  identité et tâches tardives.
- PR [#2350](https://github.com/pzia/cifereca/pull/2350) :
  type exact des variantes attestées avant projection/action ISBN.
- PR [#2357](https://github.com/pzia/cifereca/pull/2357) :
  score publiquement affiché, filtrage des valeurs invalides (statut CI à
  vérifier séparément ; **ne constitue pas une recette de validation GTK**).

**À valider hors de ce document** : recette GTK 1366/1600
sélection→détail/actions, focus, clavier, scroll, retour, désactivation puis
réactivation, identité changée pendant callbacks ; comparaison structurelle
après livraison du helper commun #1598, et gates CI au SHA final. Ne pas
marquer ces critères comme acquis sur la base de tests headless uniquement.
