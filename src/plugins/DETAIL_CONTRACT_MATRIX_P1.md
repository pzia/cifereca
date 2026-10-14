# Matrice de conformité des détails bibliographiques — Plugins P1 #1599

État du `main` et PR Plugins vérifiés le 14 octobre 2026. Cette matrice
complète #1596 / #1598 : elle **ne définit pas de contrat CoreUI parallèle**.
Les seuls ports de rendu autorisés sont `host.build_book_master_detail`,
`UiBookListEntry`, `UiBookDetailPresentation`, `DetailField`,
`DetailSection`, `DocumentLink` et `UiBookDetailAction`.

## Champs, sections et provenance

| Invariant | Jeunesse (`youth_page.py`) | Constellations (`constellations_page.py`, `constellation_view_model.py`) | Recommandations (`recommendations.py`) |
| --- | --- | --- | --- |
| Liste / panneau public | `build_book_master_detail` → `show_rows` / `show_detail` | `build_book_master_detail` → `show_detail`, voisins dans `FlowBox` métier | `build_book_master_detail` → `show_rows` / `show_detail` |
| Titre et auteurs | Titre, sous-titre, chaque auteur en `DetailField(kind='author')` | Titre/sous-titre ; auteurs individuels après #1611 (non fusionnée) | Titre/sous-titre ; auteurs individuels et dédoublonnés après #1608 (non fusionnée) |
| Couverture | URL de liste et détail filtrée par `public_url` ; repli natif du renderer | Aucune couverture explicitement projetée dans le détail sur `main` ; **ne pas inventer d'URL** | URL de liste et détail issue de la même validation conservatrice après #1608 |
| Sections métier | **Âges attestés**, puis **Détail** (ISBN, source, langue, format, type, thèmes, éditeurs, année, collection, résumé, sources bibliographiques) | **Provenance** ; voisinage et raisons attestées/inférées dans les cartes dédiées | **Recommandation** (affinité, classement, raisons), **Catalogue**, **Notice**, **Éditions et sources distinctes**, option **Reformulation générée — à vérifier** |
| Multivalués | Âges, auteurs, sources, formats, thèmes, éditeurs : une ligne par valeur attestée | Auteurs/sources agrégés sur `main`, lignes typées après #1611 ; raisons par voisin préservées | Sources agrégées, variantes de source séparées et bornées ; auteurs en lignes séparées après #1608 |
| Badges | Jusqu'à 3 âges, avec type conservé ; âge inconnu sur liste si non attesté | Disponibilité issue du graphe ; possession publique non attestée corrigée par #1613 ; aucun droit d'acquisition inféré | Affinité, source, « Provisoire » tant que calcul incomplet |
| Liens publics | Notice `friendly_url` ou `notice_url` expurgée ; jamais signé/local | Lien `friendly_url` ; filtrage réseau/paramètres et repli public renforcés par #1606 | « Voir en ligne » uniquement via `_external_book_url` ; couverture expurgée par #1608 |
| Auteur inconnu / âge inconnu | Jamais de date/âge inventé ; message « Aucune tranche d’âge attestée » | Ne pas fabriquer d’auteur ou de provenance | Sous-titre vide autorisé ; ne pas fabriquer de possession ou de droits |
| Livre homonyme | ISBN et référence source distincts pour éditions distinctes | `BookRef` / clé normalisée : deux éditions peuvent rester distinctes | ISBN **strictement attesté** par la notice de source, dédoublonné entre credentials, pas d'action directe inventée |

## Actions, identité et cycle de vie

| Contrôle | Jeunesse | Constellations | Recommandations |
| --- | --- | --- | --- |
| Action livre vers Core | `open_book_detail(book_ref, identity_id=…)` seulement sur clic ; échec si non ambigu | `open_book_detail(BookRef, identity_id=…)` pour centre et fiche d'origine | Recherche ISBN locale explicite `search_books(..., online=False, identity_id=…)` ; actions d'acquisition laissées à Recherche |
| Aucune acquisition implicite | Lien public et âge ne prouvent pas la possession ; enrichissement bibliographique uniquement sur action explicite | Relation, badge et noeud ne prouvent pas un droit ; aucune acquisition déclenchée par rendu | Score, proximité, ISBN et texte IA ne prouvent pas la possession |
| Sélection 0/1/N | `empty_detail` si résultat ou sélection vide ; génération des collectes | `empty_detail` si aucun centre, voisins paginés/bornés, A→B→C via `Navigation` | `empty_detail` si aucun résultat ou sélection ; clé absente réinitialisée dans #1608 |
| Identité | Cache requêté avec `identity_id`, callbacks de collecte et bibliographie contrôlés | Échantillon du broker par identité, pending contextual conservé dans le bon profil, retour origine explicite | Profil source dans le calcul, recherche ISBN locale capture identité, génération, sélection |
| Actions périmées / async | Génération + livre + clé + identité capturés pour les actions de détail après #1609 ; tâches rejetées si contexte différent | `generation`, identité et `disposed` filtrent les callbacks tardifs ; chargements annulables | `_generation` et `_llm_generation` invalident les callbacks ; reset au changement de sélection |
| Désactivation / fermeture | `disposed` et génération ; annulation des tâches et désabonnement | `disposed`, génération, annulation de tâche, désabonnement | `dispose()` invalide générations/LLM, annule tâches et sources GLib |
| Interface compacte | Master/detail natif ; champs d'âge distincts, sans moteur de rendu | Master/detail natif + voisinage séparé dans `FlowBox` | Master/detail natif ; liste de résultats virtualisée |
| URLs hostiles | Rejet schéma, IP privée, token, fragment, userinfo, host local | Rejet supplémentaire des URLs signées/locales avec #1606 | Rejet des liens externes dangereux ; couvertures alignées après #1608 |

## Divergences prouvées, correctifs, validations

- **Constellations / sécurité des liens** : PR [#1606](https://github.com/pzia/cifereca/pull/1606) — validation des liens publics, repli vers une notice sûre, métadonnées malformées ; tests `tests/test_detail_public_notice_contract_p1.py`.
- **Recommandations / détail cohérent et sélection périmée** : PR [#1608](https://github.com/pzia/cifereca/pull/1608) — couvertures filtrées identiquement en liste/détail, auteurs multivalués, état vide pour clé disparue ; tests `tests/test_recommendation_detail_projection_contract_p1.py`.
- **Jeunesse / actions obsolètes après sélection ou changement de profil** : PR [#1609](https://github.com/pzia/cifereca/pull/1609) — vérification à l'exécution des actions locales ; tests `tests/test_youth_detail_stale_actions_p1.py` et `test_youth_page.py`.
- **Constellations / auteurs et sources multivalués** : PR [#1611](https://github.com/pzia/cifereca/pull/1611) — auteurs/sources en champs typés séparés, raisons spécialisées du graphe préservées ; ordre final à valider avec les conventions publiques de #1598.
- **Constellations / possession par provenance** : PR [#1613](https://github.com/pzia/cifereca/pull/1613) — un `owned=True`, `local_path` ou état local issu d'un catalogue public ne devient plus un badge « possédé » ; seule la ligne du broker bibliothèque identité-scopé l'atteste.
- **Couverture Constellations** : absence sur le modèle de détail constatée ; ne pas la synthétiser depuis une ressource d'acquisition. Compléter uniquement si un champ bibliographique public vérifiable existe.

Les PR ci-dessus sont des **correctifs livrés en branches** : ne pas confondre « CI de branche verte » et « fusionné dans `main` ». La qualification réelle des adaptations n'est complète qu'après intégration du contrat commun CoreUI #1598.

## Gates de sortie à exécuter par CI/CD / UX

1. PR CI Plugins et packaging sur chaque dernier HEAD ; réexécution après intégration des modifications et de #1598.
2. **E2E Smoke GTK 1366 et 1600**, plugins installés/activés : Jeunesse liste→détail→retour ; Recommandations 0/1/N et changement rapide ; Constellations A→B→C→origine ; focus, tabulation, scroll, liens.
3. Changement d'identité pendant rendu/recherche, remplacement de cache, désactivation/réactivation de plugin, fermeture pendant une tâche puis callback tardif ; aucune action sur autre compte.
4. Comparaison des valeurs communes (titre, auteurs, couverture, source, format, langue) avec Recherche/Bibliothèque après le helper de #1598, en conservant les sections d'âge/voisinage/affinité.
5. Absence de `cifereca.gui` privé, de renderer parallèle, de requête réseau/d'acquisition à l'affichage et de droits déduits d'un score, lien ou rapprochement titre/auteur.

La convergence **métier** des actions Recommandations reste dans #1354 ; la densité responsive relève de #566. Ne pas fermer #1596 avant validation croisée avec #1598 et les gates du SHA intégré.
