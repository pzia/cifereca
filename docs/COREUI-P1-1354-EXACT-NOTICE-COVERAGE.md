# P1 #1354 — Notices exactes Recommandations → Recherche (lot borné)

## Fonctionnement

Une recommandation peut regrouper plusieurs variantes de notices, éditions
et comptes. L'existence d'une recommandation, d'un ISBN ou d'un score
d'affinité **n'atteste jamais** une disponibilité, un droit, un achat ou
une autorisation de téléchargement.

Dans le détail Recommandations, **seule une variante avec coordonnées
originelles complètes** (`provider / credential / record_id / docbase /
source_collection`) peut proposer « Voir les actions de la notice N dans
Recherche ». Le plugin interroge passivement le contrat public
`UiLoadedSearchNoticeHost.can_open_loaded_search_notice` et, **sur clic
explicite uniquement**, appelle `open_loaded_search_notice`. Le Core
recontrôle l'identité actuelle, la génération de la Recherche, la
présence de la notice exacte, l'unicité, sa visibilité et l'activation
du provider. Il sélectionne ensuite **le même DetailPanel Recherche**
avec ses règles d'action, de droits et de confirmation.

## Ce lot

- Inspection **purement locale** des **256 premières variantes au maximum**,
  au lieu de 64 ; **16 notices exactes distinctes** affichées au maximum.
- Déduplication par les **5 coordonnées source réelles**, plutôt que par
  ISBN/titre : un même enregistrement décrit avec plusieurs ISBN n'affiche
  pas plusieurs boutons, mais deux comptes distincts restent distincts.
- Les ISBN proposés pour une recherche locale explicite sont vérifiés dans
  la même fenêtre bornée. Les coordonnées malformées ne deviennent pas
  actions. Limite non exhaustive **explicitée dans le détail**, y compris
  si les premières 256 variantes sont toutes incomplètes.
- Vrai test GTK4/XTEST Recommandations 1366/1600 : clic clavier sur
  « Voir les actions », vérification de l'appel au résolveur **Core réel**
  avec un modèle Recherche déjà chargé **synthétique**, et retour à la liste
  sans résurrection du bouton précédent.

Aucun appel réseau, recherche distante, holdings, emprunt, fichier ou LLM
n'est déclenché par l'affichage. Le clic sur la notice déjà chargée suit le
comportement normal de sélection Recherche : le chargement éventuel des
holdings demeure une **décision du Core** sous ses garde-fous habituels.

## Limites / recette suivante

- Les notices **absentes** des résultats déjà chargés dans Recherche restent
  inaccessibles par ce contrat. Les recherches ISBN/titre existantes ne sont
  que des explorations locales explicites ; elles ne désignent **pas**
  automatiquement une notice exacte.
- La fenêtre de 256 variantes et les 16 choix affichables ne sont pas
  une couverture complète. Les autres variantes demandent une navigation
  / pagination dédiée et mesurée.
- L'E2E utilise des données de catalogue/identité factices et une sélection
  Core enregistrée : il **ne prouve pas** les autorisations de prêt ou la
  latence réelle d'un fournisseur.
- L'acceptation finale de #1354 exige une comparaison GTK avec la
  **même notice exacte** depuis Recherche et Recommandations, pour les cas
  disponible, indisponible, téléchargeable, droits incertains et
  multi-comptes, puis la recette sur installation.


## Pagination explicite des éditions attestées — deuxième tranche

Le détail Recommandations propose maintenant les commandes **« Notices
suivantes » / « Notices précédentes »** lorsque plus de 16 variantes
distinctes validées sont présentes parmi les **256 premières** inspectées.

- **16 notices par page**, pagination strictement locale, sans nouvelle
  recherche, appel fournisseur, LLM ou acquisition.
- Les notices répétées avec plusieurs ISBN comptent une seule fois ;
  deux comptes ou éditions originelles distinctes restent distincts.
- Les labels indiquent le rang réel de la notice et
  `page courante / nombre de pages`. Si la liste source comporte plus de
  256 éléments, l'avertissement de couverture partielle reste affiché.
- Toute nouvelle sélection/identité/génération de classement réinitialise la
  page et révoque les callbacks précédents. Les boutons « Voir les actions »
  continuent d'appeler uniquement le détail **Core Recherche** d'une notice
  exacte **déjà chargée**, lorsque ce contrat confirme sa présence.
- Aucune action emprunt/téléchargement n'est directement fabriquée par le
  plugin. Le numéro de page n'ouvre aucun droit ni disponibilité.

Recette simulée : notice n°17 puis n°33 accessibles sans masquer les
16 premières, retour arrière, disparition des boutons antérieurs et
revocation après changement de profil ou classement. La recette métier
réelle du ticket #1354 reste à effectuer pour les notices non chargées,
les droits et les confirmations de prêt.
