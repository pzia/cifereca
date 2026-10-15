# CoreUI P1 #1598 — matrice des vues de détail (15 octobre 2026)

## Frontière de projection commune

Le modèle **public existant** est `UiBookDetailPresentation` avec
`DetailSection`, `DetailField` et `DocumentLink` dans
`cifereca.plugin_api`. Le générateur neutre
`gui/detail_model.py::book_detail_presentation` produit ce modèle ;
`gui/detail_panel.py::DetailPanel.show_presentation` en est le rendu GTK
canonique. `MasterDetailPage` et `PublicBookMasterDetailView` sont
des adaptateurs, **pas** des panneaux bibliographiques concurrents.

La présente modification réunit **uniquement les sept champs préexistants**
des deux sections « Essentiel » (Source, Format, Langue, Disponibilité)
et « Bibliothèque » (Identité, Fichier local, État acquisition) dans
`gui/local_book_detail_sections.py::local_book_detail_sections`.
Les deux contrôleurs locaux appellent ce helper ; aucun widget GTK,
action de prêt, réseau, résolveur de droits, disque ou état d'identité
global n'y est introduit.

| Origine | Projection et rendu | Particularités explicites | Identité / actions / async |
|---|---|---|---|
| Recherche locale | `search_details._search_selected` → `local_book_detail_sections` → `book_detail_presentation` → `DetailPanel` | Repli « Disponible hors ligne » **seulement** pour `state=local` sans disponibilité attestée ; notice EPUB, facettes, disponibilité de catalogue à la demande | Résolution de l'identité source, chemin local inchangé ; enrichissement de cache déjà géré ; aucune recherche distante déclenchée par le helper |
| Bibliothèque | `library_detail_controller.library_selected` → même helper et même projection/panneau | Disponibilité fournie par le service, sans repli Recherche ; document_links sécurisés, placements et possessions multi-identités dédupliqués seulement pour la section de présence | Prêt exact délégué au rendu de prêt ; actions locales, recommandations et facettes inchangées |
| Recherche distante | `DetailPanel.show_search` / `show_item_detail` → `book_detail_presentation` | « Support », « Notice », « Disponibilités » et exemplaires ; état « Non vérifiée » avant retour | Callback holdings attaché aux générations de requête/sélection/identité ; aucun helper de livre **local** appliqué |
| Récents | `recent_detail_controller.recent_selected` → `DetailPanel.show_summary` → **même** `book_detail_presentation` et `show_presentation` | Section distincte « Récupération » : Fichier, Récupéré, Provenance, Chemin ; badges Local + fournisseur | Actions locales et facettes conservées ; changement de rendu inutile puisque le chemin actuel converge déjà |
| Prêts | `DetailPanel.show_loan` → `book_detail_presentation` | « Essentiel » + « Prêt » : retour prévu, bibliothèque, état, support ; restitution ADEPT propre aux droits de prêt | Téléchargement uniquement si `can_download_loan` ; ne pas réutiliser la disponibilité d'un fichier local |
| Jeunesse | Plugin via `host.build_book_master_detail` → `PublicBookMasterDetailView.show_detail` → `DetailPanel` | Âges attestés, critères et provenance spécifiques ; métadonnées publiques fournies par le plugin | Responsabilité de conformité et d’identité de la PR Plugins #1599 ; pas d'import de `gui` privé à introduire |
| Constellations / Recommandations | Même contrat public `UiBookDetailPresentation` / adaptateur hôte / `DetailPanel` | Voisinage et affinité spécifiques ; décisions d'acquisition Recommandations propriétaires de #1354 | Droits et provenance propres aux plugins ; contrôle transversal couvert par #1599 |

## Invariants pour les sept parcours

- **Ordre** : sections « Essentiel », « Bibliothèque » pour les livres locaux ; les sections spécialisées sont ajoutées par leur contrôleur. Le moteur commun omet les *cellules* vides au rendu sans inventer de faits ni déplacer les champs multivalués.
- **Identité** : `identity_by_id` reçoit strictement l'identifiant du livre ; valeur de repli = identifiant fourni, jamais l'identité actuellement affichée. Les droits et fichiers ne sont pas transférés entre profils.
- **État d'acquisition** : seul le label nominal « À jour » est masqué ; les autres états sont inchangés. Le helper ne télécharge, ne renouvelle et ne vérifie rien.
- **Liens** : seul l'adaptateur Bibliothèque continue d'ajouter `document_links(item)`, avec contrôle URL au panneau final ; la projection commune ne construit ni ne désigne d'URL.
- **Disponibilités et asynchronisme** : la recherche distante et les prêts restent hors helper ; les callbacks tardifs et l'absence de disponibilité ne deviennent pas une preuve de possession. Le simple clic sur une fiche n'acquiert pas un livre.
- **Résumé et focus** : `DetailPanel.show_presentation` préserve l'ordre résumé/après Essentiel, la barre d'actions, le focus logique, le défilement et les changements de largeur. Les tests GTK/E2E restent requis pour qualification finale.

## Recette / dépendances de clôture

Tests headless de comparaison : mêmes livre/identité/métadonnées → mêmes champs
et ordre en Recherche locale / Bibliothèque ; indisponibilité et liens restent
différents lorsque l'origine le justifie ; livre vide, langue multivaluée,
état non nominal, absence de recherche réseau.

À valider par UX/CI-CD **sur le HEAD fusionné** : fenêtres 1366/1600,
0/1/N notices, Recherche locale/distante → Bibliothèque → Récents → Prêts
→ Jeunesse/Constellations/Recommandations → retour/focus, changement de profil
et callbacks tardifs. #1598 ne clôt pas la recette du ticket parent #1596 ;
la conformité des extensions métier plugins relève de #1599.
