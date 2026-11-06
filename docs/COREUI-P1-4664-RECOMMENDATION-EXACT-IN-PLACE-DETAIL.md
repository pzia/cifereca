# P1 #4664 — Détail fournisseur exact dans Recommandations, sans changer d'onglet

## Livraison incrémentale

Une recommandation peut représenter plusieurs variantes et comptes. Le
**même `DetailPanel.show_item_detail` que dans Recherche** peut désormais
être ouvert dans le panneau Recommandations pour **une notice exacte déjà
chargée, visible et admissible dans la Recherche Core**.

L'action « Consulter disponibilités et actions de la notice N ici » est
proposée en plus de « Voir les actions dans Recherche » lorsque l'hôte
expose le port public optionnel
`UiExactSearchDetailHost.present_loaded_search_detail_in_place`.
Le choix d'édition et de compte est **explicite** ; pas de première variante,
de correspondance titre/ISBN, de credential implicite ou de score converti
en droit. Les options de navigation existantes ne régressent pas.

## Frontière des responsabilités

1. Le plugin fournit uniquement les coordonnées **identité / fournisseur /
   credential / docbase / collection / record_id** de la variante
   originelle déjà attestée, un `UiBookMasterDetailView` créé par Core, les
   sections contextuelles et un prédicat d'invalidation de sa sélection.
2. Core identifie la notice exacte via `loaded_notice_key`, lit le
   `SearchResult` **de son propre cache visible** et vérifie le compte et
   la session du profil en vigueur. L'absence ou l'ambiguïté refuse l'action.
3. **Sur clic utilisateur seulement**, le `TaskRunner` Core déclenche
   `service.holdings(item, query, force=False)` dans son worker. Aucun appel
   distant n'est réalisé lors de l'affichage d'une recommandation.
4. Avant le worker puis avant la publication GTK, le Core vérifie de nouveau
   l'identité, l'enregistrement exact du compte/session, la connexion
   activée, les générations de Recherche/identité/shell et l'objet
   `SearchResult` chargé. Le plugin révoque aussi sa sélection courante
   (clé, classement, profil, page et génération). Un callback tardif est jeté.
5. Le panneau Core appelle `DetailPanel.show_item_detail(item, detail)`,
   **sans reconstruire les boutons métier dans le plugin** : décisions de
   disponibilité, emprunt, téléchargement, décorateurs et leurs propres
   confirmations restent identiques à Recherche. Les champs « Affinité »,
   « Pourquoi », « sources », rang et LLM optionnel sont conservés comme
   sections descriptives supplémentaires, jamais des preuves métier.

Les états de refus/erreur n'exposent pas d'URL fournisseur, de compte, de
jeton ou de contenu de réponse non fiable. Les sources `known_catalog`
non déjà chargées gardent le parcours sûr préexistant vers Recherche ; ce
premier lot ne les ouvre pas silencieusement en place.

## Couverture, suites et non-objectifs

Tests headless Core/Plugins : aucun GET au rendu, clic explicite seulement,
provenance d'objet Core exacte, navigation inchangée, deux variantes
distinctes, révocation A→B→A, session et connection désactivées, résultats
hors date, sections de contexte conservées, aucun handler de droit secondaire.

Reste à solder dans #4664 :
- intégration de la source `known_catalog` après validation d'un miroir
  v2 toujours actif et du chargement sur clic ;
- offre achat/gratuit, placement déjà présent et prêt via un autre profil
  (#4662) lorsqu'une capacité Core autoritative est disponible ;
- boutons persistants/revalidation UX après mutation et recette GTK
  1366/1600 (clavier/focus, états d'échec, retours).

Ce lot ne clôture ni #4664 ni #4662. Il ne modifie aucune transaction P0
#3121 ni aucun mécanisme de DRM/acquisition ni d'emprunt fournisseur.
