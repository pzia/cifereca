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
jeton ou de contenu de réponse non fiable.

## Extension : notice connue du miroir v2 actif

La tranche suivante ajoute
`UiExactSearchDetailHost.present_known_search_detail_in_place` pour les
notices non chargées en Recherche mais **connues de Core via un miroir SQLite
v2 exact, toujours actif et lié au compte/identité sélectionnés**.
`_exact_known_row` revalide la source/provenance, et
`known_catalog_search_result` reconstruit le `SearchResult` sans accepter
d'objet Search fourni par Recommandations. Cette preuve ne constitue toujours
pas un droit de prêt ni d'acquisition.

La preuve `known-v2` du clic est copiée indépendamment, y compris les
métadonnées imbriquées, pour qu'un adaptateur SQLite qui réutilise un
dictionnaire mutable ne masque pas une modification en place de l'édition,
de l'offre ou de sa provenance. Le Core compare cette preuve figée à
la lecture courante avant le GET et avant la publication des actions.

L'activation est un clic utilisateur explicite. Avant le worker et avant
l'affichage, Core recroise le miroir, l'édition, les champs de source et les
révisions capturées ; une notice modifiée, retirée ou devenue stale est
refusée sans reprendre les droits d'une autre édition. La même demande de
holdings Core et le même renderer de détail/action s'appliquent, sans
navigation d'onglet. L'ancienne navigation vers Recherche reste disponible
comme solution de repli et pour les notices non qualifiées.

### Plusieurs consultations successives du même détail

Chaque clic explicite dans un panneau Recommandations établit un jeton local
de demande. Une consultation plus récente **du même panneau** révoque les
callbacks de la précédente, même lorsque la notice, le profil, la session
et les générations Recherche sont identiques. L'ancien worker est annulé
avant le GET s'il n'a pas commencé ; s'il avait déjà fini, son résultat
et son erreur tardifs ne remplacent jamais la fiche plus récente.
Le contrôle est un état scalaire CoreUI, sans accès GTK dans le worker,
sans nouvelle mutation ni droit fournisseur. Le cache/transport conserve
ses règles habituelles de concurrence.

## Couverture, suites et non-objectifs

Tests headless Core/Plugins : aucun GET au rendu, clic explicite seulement,
provenance d'objet Core exacte, navigation inchangée, deux variantes
distinctes, révocation A→B→A, session et connection désactivées, résultats
hors date, sections de contexte conservées, aucun handler de droit secondaire.

Reste à solder dans #4664 :
- approfondissement de la recette du miroir `known_catalog` avec
  session retirée en cours de GET et plusieurs onglets simultanés ;
- offre achat/gratuit, placement déjà présent et prêt via un autre profil
  (#4662) lorsqu'une capacité Core autoritative est disponible ;
- boutons persistants/revalidation UX après mutation et recette GTK
  1366/1600 (clavier/focus, états d'échec, retours).

Ce lot ne clôture ni #4664 ni #4662. Il ne modifie aucune transaction P0
#3121 ni aucun mécanisme de DRM/acquisition ni d'emprunt fournisseur.
