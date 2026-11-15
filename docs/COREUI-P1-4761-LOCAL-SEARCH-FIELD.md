# P1 #4761 — Champ de recherche partagé, vue locale Recommandations

## Contrat livré

Le filtre **Champ** de Recherche, déjà présent dans `QueryControls`,
est réutilisé par `LocalQueryControls` en **opt-in** via
`enable_field_filter()`. Les autres consommateurs de filtres locaux
conservent le champ masqué et la sémantique historique. Le plugin
Recommandations n'importe aucun module `cifereca.gui` : il consomme
seulement la façade publique `host.build_local_query_controls(...)` et les
méthodes facultatives `enable_field_filter()` et `filter_field()`.

La recherche textuelle opère **exclusivement sur la liste de
recommandations déjà calculées**. Le changement de champ, de texte, ou
sa remise à `Champ` ne provoque **aucun nouveau classement, accès disque,
scan de bibliothèque, requête catalogue, emprunt, téléchargement ou LLM**.

* `Champ` (tous les champs) : titre et auteurs, mais aussi
  **sujets, série, collection, langue, éditeur et ISBN attestés** déjà en
  mémoire, afin de conserver la portée bibliographique de Recherche.
  Les champs supplémentaires sont bornés, et les ISBN restent limités à la
  **source exacte** quand le filtre Sources est actif. Les identifiants de
  notice, URL, chemins locaux et autres valeurs privées ne sont pas indexés.
  L'ordre diversifié demeure inchangé.
* `Titre` / `Auteur` : valeurs publiques exactes du candidat.
* `ISBN` : seuls les identifiants de livre dont la somme de contrôle a
  été vérifiée par le moteur ; un `record_id` fournisseur n'est jamais
  considéré comme un ISBN.
* `Sujet` / `Série` / `Collection` : projections bornées des seules
  métadonnées déjà en mémoire ; aucun objet fournisseur sérialisé.
* `Langue` : uniquement les codes normalisés et attestés déjà servis
  au filtre multi-langues.
* Champ mal formé : refus conservateur, aucun élargissement silencieux.

Le compteur visible inclut le champ explicitement choisi, en utilisant
le label du même widget Recherche. L'état vide est le comportement partagé
de la liste, et le filtre reste réversible par sélection `Champ` ou
effacement du texte.

## Extension P1-FINALISATION du champ global (15/11/2026)

La valeur par défaut `Champ` ne se limite plus au duo titre/auteur :
lorsqu'une notice porte des classifications et éditions déjà attestées,
l'utilisateur peut retrouver une recommandation par sujet, série, collection,
langue, éditeur public ou ISBN valide, sans devoir choisir chaque champ
successivement. **Aucun catalogue n'est interrogé à cette occasion.**

Le texte d'éditeur ressemblant à une URL privée/signée n'est pas ajouté à
l'index local. La protection des variantes ISBN entre catalogues respecte
exactement le même garde que le filtre spécifique `ISBN`, y compris sur
les recommandations dédoublonnées. Une projection manquante ou opaque
reste inconnue, jamais transformée en texte à indexer. Les valeurs de
métadonnées volumineuses sont bornées ; ce filtre n'est pas un moteur de
recherche globale ni une autorisation d'opération fournisseur.

## Sémantique des mots et expressions du filtre global (P1-FINALISATION)

Le filtre par défaut **Champ = Tous les champs** reprend la grammaire
locale de Recherche : les mots non cités sont recherchés sans dépendre de
leur ordre et peuvent se trouver dans **plusieurs champs bibliographiques
distincts** du même candidat (ex. auteur et sujet). Les mots-outils français
courants ne masquent pas les termes significatifs ; une requête composée
uniquement de mots-outils conserve ses mots et ne sélectionne pas
automatiquement toutes les lignes.

Une **expression entre guillemets** doit apparaître de façon contiguë
dans **une même valeur publique attestée** ; juxtaposer deux champs
différents ne crée pas une expression bibliographique artificielle.
Les termes et expressions peuvent être combinés, mais chaque terme doit
être attesté pour que le résultat reste visible.

Il n'y a **aucune recherche réseau ni reclassement** : le filtre utilise
uniquement les champs déjà chargés et bornés par l'adaptateur Plugin. Les
ISBN de variantes restent conditionnés au catalogue explicitement
sélectionné ; les champs privés, URI et identifiants fournisseur ne sont
pas indexés. Les champs explicitement choisis (Titre, Sujet, ISBN, etc.)
gardent leurs restrictions propres. Ce correctif ne prétend pas reproduire
le *score* de pertinence de la Recherche Core : il préserve l'ordre diversifié
de Recommandations.

### Preuve dans la vraie interface GTK (suite #6184)

Le scénario existant `recommendations-detail-1024/1366/1600` exerce
désormais `Gtk.SearchEntry` et le vrai `LocalQueryControls` : sujet
« Astronomie » + auteur « Autrice Attestée » retrouvent la même ligne avec
`astronomie autrice`, l'ordre inverse ou `"astronomie" autrice`.
`"astronomie autrice"` n'en retrouve aucune : deux valeurs distinctes
ne forment pas une expression citée. Après rétablissement de la recherche
simple, le filtre explicite Titre doit toujours masquer le sujet. Les
compteurs réels, la sélection et l'invariance du compteur de requêtes et de
la génération sont vérifiés dans **la vraie interface**, sous Xvfb/XTEST.
Les données restent synthétiques ; ceci ne remplace pas la revue visuelle
sur les mêmes notices entre pages (#774).

## Focus clavier local Ctrl+F (P1-FINALISATION)

Dans Recommandations, le raccourci **Ctrl+F** doit cibler le champ
`Gtk.SearchEntry` du `LocalQueryControls` de **cette page**, pas le
filtre de la vue Recherche hors écran. Le `MasterDetailPage` commun
expose un branchement optionnel de focus, relayé par la façade publique
`PublicBookMasterDetailView.bind_local_filter_focus` et par le
`LocalQueryControls.focus_text_filter` (uniquement si la vue est mappée).
La page plugin utilise ces deux méthodes par introspection optionnelle
pour préserver la compatibilité avec les Core plus anciens. L'ancien
mode sans barre commune conserve le focus de son champ local.

Le raccourci ne modifie **ni** le texte, **ni** les filtres, **ni** le
calcul de recommandations, **ni** les identités ou droits. Il ne peut
plus diriger Ctrl+F vers une `window.search_controls` cachée depuis
une page plugin. Le scénario réel GTK/XTEST envoie les touches
physiques `Control_L+f` depuis la liste et constate le **focus natif**
sur le champ de recherche locale, sans requête fournisseur ni
recalcul, avant de poursuivre le test de saisie clavier existant.

## Recette et frontières

`e2e/recommendations_detail_probe.py` vérifie le vrai widget GTK aux
largeurs 1366 et 1600 : recherche locale par sujet, passage au champ
Titre (aucun résultat), reset, compteur associé et absence d'appel
fournisseur ou de recalcul. Les tests Plugins couvrent chaque champ
attesté, des métadonnées malformées, les homonymes, la réversibilité,
l'ISBN et la protection contre la sérialisation d'objets.

La matrice visuelle **Recherche ↔ Recommandations** aux largeurs et thèmes
demandés dans #4761, la recette clavier #774 et les actions métier #4664
restent **distinctes**. Ne pas fermer #4761 sur cette tranche.

## Précision — ISBN des éditions originales multi-catalogues (#4761)

Une recommandation classée peut réunir plusieurs `CatalogNoticeVariant`
(source, credential, collection, docbase, record et ISBN de l'édition)
tandis que le candidat agrégé n'expose aucun ISBN, ou celui d'une
**autre** édition. Le filtre `Champ · ISBN` interroge également ces
notices **déjà calculées** et checksum-validées ; il n'ouvre aucune
nouvelle recherche catalogue et ne transforme pas une notice en droit.

Quand le filtre `Source` est actif, seuls les ISBN des variantes originales
de cette source participent à la sélection. Si des variantes typées sont
présentes, l'ISBN non attribuable du candidat agrégé est ignoré pour
éviter d'associer l'édition B au catalogue A. Les candidats historiques
sans variantes gardent leur ISBN seulement si leur `source_provider`
attesté correspond au filtre. Sans filtre source, le texte peut rencontrer
l'ISBN du candidat ou des éditions vérifiées. Limites bornées : 256 notices
inspectées, au plus 32 ISBN distincts dans le filtre ; le Détail conserve
sa limite antérieure de 4 actions ISBN. Jamais de GET, emprunt, droit ou
normalisation par titre entre deux catalogues.

La recette GTK inclut maintenant une ligne sans ISBN agrégé mais possédant
un `CatalogNoticeVariant.isbn` attesté, puis la réinitialisation du
filtre ; des tests Plugins opposent deux ISBN/éditions de sources
concurrentes et des variantes falsifiées/incomplètes.


## Bandeau de paramètres de calcul séparé des filtres Recherche

Le haut de Recommandations comporte deux surfaces distinctes :

1. **Paramètres de calcul** : profil, référence, exclusion des livres
   possédés, seuil d'affinité, diversité, `Recalculer`, et IA facultative
   seulement lorsque configurée. Une seule rangée horizontale
   `Gtk.Box` dans un `Gtk.ScrolledWindow` à débordement horizontal.
   Au rétrécissement de la fenêtre, les options ne passent plus sur une
   deuxième ligne avec un nouvel ordre de tabulation.
2. **Filtres de résultats déjà calculés** : le véritable
   `LocalQueryControls` de Recherche (Champ, texte, Sources multiples,
   Langues, Format, Type), toujours dans son propre bandeau, sous les
   paramètres. Manipuler les filtres n'appelle ni recalcul, ni fournisseur,
   ni LLM.

Un Core ancien, dépourvu de `build_local_query_controls`, conserve les
widgets locaux `Catalogue/Langue/Filtrer` dans le bandeau des paramètres
via `_append_legacy_filters`. Aucun changement des brokers, des droits,
de l'admission d'acquisition ou du calcul. L'E2E GTK 1366/1600 vérifie
le vrai parent `Gtk.Box(HORIZONTAL)`, sa fenêtre de défilement, et que
les contrôles Recherche restent des enfants séparés.
