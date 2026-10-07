# Cifereca — Plan d’action UX pour le développeur

Date : 6 octobre 2026. Statut : **analyse et plan uniquement ; aucune modification applicative**.

Les numéros entre crochets renvoient aux 24 recommandations commentées par l’utilisateur. Le point 20 et les modalités signalées « proposition » ne sont pas considérés comme validés. La distinction recherche locale/en ligne est reportée au backlog de fond.

## 1. Base de travail et responsabilités

Analyse du Core installé `0.9.44+dev.1`, du plugin Accueil `1.0.11` et du store de recette : Gallica `1.2.10`, Paris `1.2.14`, BnF `1.0.7`, Gutenberg `1.2.10`, Kobo `1.2.7`, Numilog `0.1.8`. Référence de la recette précédente : `main b036d536b5dd5434601f9bfdbaa9d2526578ffc9`, **pas une vérification du main actuel**. Le clone local est partiel ; rapprocher les changements du dépôt complet avant développement.

Dans ce document, les chemins `src/cifereca/...` désignent les modules correspondants de la distribution Core inspectée. Ils ne sont pas tous présents dans le clone local.

| Responsable | Périmètre |
|---|---|
| **Core métier** | Identités, statistiques, états de chargement et de connexion, agrégation des réponses fournisseurs. |
| **Core GTK** | Fenêtre, listes partagées, filtres, notifications, pages standard, gestionnaire de plugins, paramètres. |
| **UI intégrée au Core** | `builtin_ui/cifereca_builtin_ui.py` : `Pages`, `SystemPages`, `Widgets`. Leur exposition comme plugins ne les transforme pas en packages externes. |
| **Plugin Accueil** | `plugins/home-page/cifereca_home.py` : carte, explications, actions proposées via le host Core. |
| **Plugins fournisseurs** | Paris/Gallica/etc. : adaptation des protocoles et remontée d’erreurs ; aucun calcul parallèle des compteurs ou correctif de mise en page. |
| **Banc de test** | `simulation/datasetctl.py`, profils TOML et harnais GTK externe : dimensions, plein écran, contrôles géométriques et snapshots. |

## 2. Core — correctifs et évolutions

### C1 — Corriger la cohérence des compteurs [1]

**Cause confirmée dans cette recette.** Les 100 lignes `placements` locales ont `identity_id=NULL`. `catalog.py:LocalCatalog._path_hints()` conserve volontairement une identité vide pour les fichiers hors arborescence d’identité déclarée. `services.py:library_entries()` rattache ces entrées à la première identité pour leur affichage ; `identity_statistics()` retient seulement les emplacements dont l’identité correspond strictement. Bibliothèque affiche donc 100, Accueil et bandeau 0. Ce n’est pas seulement un rafraîchissement manquant.

**Correction proposée :** extraire une règle commune d’identité effective, appliquée aux projections locales, statistiques et récents. Préserver la compatibilité actuelle : les fichiers non attribués apparaissent pour l’identité par défaut uniquement ; ne pas réécrire silencieusement leur propriétaire en base. `recent_acquisitions()` admet actuellement les identités vides pour toute identité : inclure ce cas dans la correction.

Compter les artefacts locaux distincts, pas les chemins, les droits distants ni les résultats de recherche. Alimenter `dashboard_state()`, `MainWindow._refresh_statistics()` et le plugin Accueil avec ce même résultat ; notifier leur actualisation après scan/import/synchronisation/changement d’identité. Ne pas appeler une projection susceptible de déclencher du réseau depuis le rendu des compteurs.

**Recette :** 100 fichiers non attribués → 100 livres pour l’identité par défaut, 0 pour une autre ; deux emplacements du même artefact → un livre ; fichiers explicitement attribués, absents et droits distants testés séparément. Ne pas exiger que le total fédéré de Bibliothèque égale toujours le compteur « livres locaux » : leurs périmètres peuvent différer et doivent être explicités.

### C2 — Garantir la taille effective, puis refaire la revue [4, 15]

`gui/main_window.py:MainWindow.__init__()` déclare un minimum de **760 × 540**, mais la recette a enregistré une fenêtre de **1 337 px** sur un écran de **1 300 px**. Les 37 px coupés ne suffisent pas à attribuer tous les défauts à la page Bibliothèque. L’origine exacte de la contrainte effective reste à mesurer.

- Cible : minimum effectif **1 024 px de largeur** ; modifier la demande de fenêtre seule ne suffit pas.
- Instrumenter `Gtk.Widget.measure()` et les allocations : shell, sidebar, `view_stack`, pages masquées, `QueryControls`, `MasterDetailPage`, barres d’actions. Examiner l’homogénéité de `Gtk.Stack`, sans la désactiver aveuglément.
- Examiner `gui/action_widgets.py:AdaptiveActionBar._resize()` : sa demande de largeur dépend du parent. Écarter une boucle de contraintes ; privilégier la réduction en icônes avant tout défilement d’actions.
- Tester en **plein écran réel 1 366 × 768**, échelle GTK 1, puis en fenêtre de largeur 1 024. Prévoir le défilement vertical à 768 px de hauteur.
- Ne figer une nouvelle répartition des colonnes qu’après cette mesure. La proposition antérieure 190 / 450–480 px est une piste, pas une exigence validée.

**Recette :** toutes les commandes restent accessibles ; aucun widget n’impose une largeur supérieure à la fenêtre. Distinguer rognage de la fenêtre par l’écran et débordement interne des cellules.

### C3 — Une cellule de livre commune sans débordement [3]

**Code :** `gui/book_widgets.py:BookListCell.__init__/bind/unbind`, `attribute_tag()` ; `gui/main_window.py:_attach_row_facets()` et fabriques de `MasterDetailPage`.

**Constats :** les facettes sont centrées ; l’auteur peut devenir un badge et masquer le sous-titre. La limite `max_width_chars(28)` n’est appliquée qu’au chemin `Gtk.Label`, pas aux badges cliquables `Gtk.Button`. Le titre possède déjà wrap, deux lignes et ellipsisation : conserver le contenu complet en tooltip, mais vérifier sa largeur allouée.

**Règle demandée :** couverture à gauche ; titre en tête ; auteur dessous ; badges ensuite, alignés au début et placés successivement sur la ligne puis la suivante. Aucun contenu ne dépasse la cellule, horizontalement ou verticalement.

**Paramétrage proposé :** titre sur deux lignes maximum, auteur sur une ligne, badges sur deux lignes maximum. Borner chaque badge par la largeur disponible et une limite commune ; utiliser un `Gtk.Label` ellipsisé comme enfant des boutons également. Réserver la place de `…` si toutes les facettes ne tiennent pas ; tooltip contenant les valeurs omises. Chaque badge tronqué expose son contenu complet au survol et au clavier ; `…` peut aussi ouvrir un popover accessible. Conserver les actions des badges.

Ne pas imposer une hauteur de ligne inférieure à la hauteur réellement mesurée. Préserver la virtualisation, l’annulation des requêtes de couverture et les gardes de recyclage dans `bind/unbind` ; réinitialiser tooltips, callbacks et facettes masquées.

**Recette :** titres sans espaces, auteurs longs/multiples, 0/1/10 badges, badge plus large que la cellule ; réduction/agrandissement et défilement rapide. Contrôler les allocations réelles, pas seulement une capture visuelle.

### C4 — Notifications additives en bas d’écran [5]

**Code :** `gui/main_window.py:toast`, `_task_started/_task_progress/_task_finished`, `_hide_activity_feedback`, `run_search` ; `gui/task_runner.py`. Une barre basse existe déjà, mais elle affiche principalement la tâche courante et disparaît après 4 ou 8 secondes ; les toasts constituent un canal séparé.

**Proposition à arbitrer :** conserver une ligne basse compacte avec tâche active et compteurs de notifications, ouvrant un historique dépliable. Ajouter les événements terminés sans remplacer les précédents ; mettre à jour un événement de progression par identifiant d’opération, plutôt que créer un message à chaque lot. Dédupliquer les erreurs récurrentes avec compteur d’occurrences ; borner l’historique de session.

Chaque événement porte opération, identité, source, heure, gravité et résultat partiel/final. Une recherche ancienne ne doit pas actualiser les résultats ou le statut courant après changement de requête/identité. Son bilan reste consultable dans l’historique. Les erreurs bloquantes conservent un dialogue lorsque nécessaire.

**Recette :** tâches concurrentes, échecs partiels, changement de page, messages longs ; aucun masquage des dernières lignes de la liste ni croissance illimitée du pied de page.

### C5 — Filtres fermés compacts sur une seule ligne [6]

**Code :** `gui/query_controls.py:MultiSelect`, `QueryControls` ; `gui/query_model.py:QueryState`. Le `FlowBox` actuel permet le retour à la ligne et les boutons affichent notamment « Forme · Sources : Tous ».

Au repos, afficher seulement le nom court : **Source, Format, Langue, Champ, Type** ; signaler une sélection active par un compteur compact. Dimensionner chaque contrôle au libellé. Lorsque la somme ne tient plus, remplacer progressivement les libellés par des icônes avec tooltip et nom accessible, tout en conservant les cinq contrôles sur une ligne. Ne changer ni les valeurs ni les callbacks lors du passage texte/icône ; rétablir les libellés quand l’espace revient.

La recherche locale/en ligne conserve son comportement et ses commandes : **refonte de cette distinction hors périmètre, backlog de fond**.

### C6 — Récents et actions locales [7, 8]

**Code :** `services.py:recent_acquisitions()`, `gui/main_window.py:_render_recent`, `_add_local_book_actions`, `DetailPanel`.

Regrouper Aujourd’hui / Hier / Plus ancien, afficher la date et une seule provenance. La projection trie actuellement par `placement.updated_at` : vérifier que cette date correspond bien à la matérialisation, sinon exposer une date dédiée ; ne pas présenter une mise à jour d’index comme une acquisition nouvelle.

Prioriser « Ouvrir le livre » et « Afficher le dossier ». Afficher le nom du fichier ; chemin complet via tooltip/détail dépliable et Copier. Conserver les contrôles d’accès du Core et son attribution d’identité corrigée en C1. Réutiliser la cellule C3 sans casser le modèle virtualisé pour les en-têtes de dates.

### C7 — États des emprunts, historique et réservations [9–14]

**Code :** `services.py:refresh_identity_loans`, `refresh_history`, `refresh_bookings` ; `gui/main_window.py:refresh_loans`, `_render_loans`, `refresh_history`, `_populate_history`, `refresh_bookings`, `_populate_bookings`, `_show_route` ; `MasterDetailPage`.

Utiliser un état explicite **non chargé / chargement / prêt / vide confirmé / partiel / erreur**, associé à l’identité, au compte et à une génération de requête. Conserver les derniers résultats lors d’un échec, en indiquant leur date. Ne pas transformer compte absent, capacité non supportée ou erreur en liste vide réussie.

Les prêts sont agrégés par identité ; historique/réservations utilisent actuellement le compte courant. Afficher ce périmètre et sélectionner un compte compatible, ou proposer un sélecteur explicite. Ne pas conclure à un bug du plugin Paris sur le seul état vide : vérifier d’abord compte connecté, capacités, requêtes et réponses.

**Bascule demandée :** après un chargement réussi, si le filtre **Numériques par défaut** ne trouve aucun prêt numérique, sélectionner automatiquement **Tous**, synchroniser le bouton actif et notifier brièvement la bascule. Ne pas répéter ce comportement si l’utilisateur a choisi explicitement Numériques. Ne pas déclencher la bascule sur une erreur ni confondre un filtre de langue vide avec une absence de prêts numériques.

Remplacer le double panneau vide par un seul état contextualisé ; masquer le détail sans sélection. Historique : supprimer « Ouvrez cet écran » lorsque la page est déjà ouverte, afficher Charger/Réessayer et dernière mise à jour. Réservations : proposer Actualiser et Rechercher un livre. Réutiliser les mêmes états et composants pour les trois pages.

### C8 — Synchronisation et outils [16–18]

**Code :** `gui/main_window.py:_build_sync_page/_refresh_sync_page`, `_build_tools_page/_refresh_tools_page` ; brokers Core de synchronisation et de maintenance.

Nommer l’interrupteur « Inclure dans la synchronisation » et exposer son périmètre. Le dernier succès et l’erreur sont déjà rendus lorsqu’ils existent : compléter par dernier essai, progression et quantités réellement renvoyées. « Actualiser l’état » reste distinct de « Synchroniser les données » ; ne pas inventer un bilan absent du contrat fournisseur.

Outils : message par défaut « Aucun outil de maintenance actif », puis précision si outils désactivés, non installés ou indisponibles. Ajouter « Gérer les plugins de maintenance ». Ne pas activer automatiquement un outil. Le test `--no-plugins` explique à lui seul certains états vides.

### C9 — Gestionnaire de plugins [19, 20]

**Code :** `gui/plugin_manager_page.py:refresh`, `_plugin_row`, `_confirm_remove`, `_priority`.

**Accepté [19] :** remplacer les actions secondaires répétées par un menu **hamburger à trois barres** (`open-menu-symbolic`, pas une ellipse). Garder activation et configuration principales visibles. Déplacer Désinstaller dans ce menu, en conservant la confirmation et la portée **package entier**. Nommer le champ « Priorité » et expliquer sa portée par tooltip/info dépliable.

**À expliciter/valider [20] :** il s’agit uniquement de retrouver un plugin dans la liste. Proposition : champ de recherche sur nom/description/identifiant et filtre d’affichage Tous / Actifs / Désactivés / À examiner. Exemple : saisir « Kobo », puis « À examiner », affiche ses plugins bloqués/en erreur/incompatibles. **Cela ne change aucune activation.** Définir séparément « activé » et « prêt » ; un plugin activé mais bloqué doit rester visible dans À examiner. Ne pas implémenter ce point comme un arbitrage déjà acquis.

### C10 — Paramètres et état du système [21–24]

**Paramètres :** `gui/main_window.py:_build_settings_page`, fonctions internes `save_settings` et `save_and_reconnect`. Mettre identités et comptes avant l’inventaire fournisseur ; déplacer les capacités techniques dans Diagnostic. Garder Enregistrer et Reconnecter accessibles selon C2 ; suivre l’état modifié, n’effacer cet état qu’après sauvegarde réussie, afficher son résultat et conserver les valeurs saisies en cas d’échec.

**Système :** `system_status.py:system_status()` et `builtin_ui/cifereca_builtin_ui.py:SystemPages.build`. Le compteur « prêts » actuel décrit le chargement des plugins, pas le fonctionnement des services : c’est la cause du bilan vert malgré des parcours en échec. Séparer **installation/chargement**, **connexion/authentification**, **dernière opération**, avec date et état inconnu/non testé. Ne pas lancer des appels réseau pendant le rendu ; bouton de contrôle explicite via `TaskRunner` si nécessaire.

Présenter d’abord les anomalies, proposer un filtre des composants désactivés et un accès au diagnostic. Les dates d’installation inconnues restent secondaires et ne doivent jamais être inventées. L’éventuel plugin externe `diagnostics-page` peut réutiliser ce modèle ; il n’est pas propriétaire du correctif de `SystemPages`.

## 3. Plugin Accueil — périmètre et actions [2]

**Code :** `plugins/home-page/cifereca_home.py:build_page`, `tile`, `attention_items`, `OverviewController`. Garder la largeur de carte. Le plugin consomme `host.identity_overview()`/le broker d’identité : il ne corrige pas les statistiques lui-même.

Afficher le périmètre des chiffres et de la synchronisation via **tooltips ou information dépliable** : identité concernée, inventaire local distinct, prêts observés éventuellement partiels, date, sources et fenêtre des statistiques d’actions. Les données manquantes restent « Non actualisé »/« Inconnu », jamais zéro par défaut.

| Élément | Proposition d’action | Support à prévoir |
|---|---|---|
| Livres / catalogue | Ouvrir la bibliothèque de l’identité, éventuellement filtrée par source | `open_identity_library/open_identity_view` existent. |
| Prêts non actualisés | « Actualiser les prêts », puis afficher leur page | Exposer une opération ciblée via le host ; ne pas appeler la synchronisation générale, plus large. |
| Source à connecter | Ouvrir le compte concerné dans Paramètres | Ajouter navigation ciblée compte/identité ; conserver le contexte. |
| Erreurs / actions requises | Déplier le bilan puis proposer Réessayer pour l’opération en échec | Actions structurées et autorisées fournies par le Core, pas déduites d’un texte d’erreur. |
| Profil ADE à configurer | Ouvrir les réglages ADE de l’identité | Navigation Core ciblée ; aucune création/autorisation automatique. |

Étendre si nécessaire `plugin_api/models.py:UiIdentityHost/UiIdentityViewHost` et `MainWindow._UiHost`, avec détection de capacité et repli explicatif pour les anciens Core. Point concret : `_UiHost.open_plugin_settings()` cherche actuellement `PluginManagerPage.focus_plugin()`, absent de la version inspectée ; implémenter ce ciblage avant de promettre un accès direct au bon réglage.

## 4. Plugins fournisseurs — intervention conditionnelle

Pour Paris, vérifier `cifereca_paris.py` et `cifereca_paris_client.py` seulement après validation du routage de compte Core. Pour Gallica, distinguer résultats EPUB et erreur audio partielle. Les providers doivent remonter des erreurs contextualisées et des résultats normalisés ; les états, notifications et décisions de présentation restent dans le Core.

La recette précédente avait cinq services sains, mais cela ne valide pas l’intégration de leurs parcours. Comparer les réponses brutes des pseudo-services aux objets retournés par les plugins puis à l’IHM : 30 prêts, 50 historiques et 15 réservations configurés côté Paris, avec filtres et compte compatibles explicités.

## 5. Ordre de livraison et recette

| Lot | Contenu | Critère de sortie |
|---|---|---|
| A | C1 + diagnostic C2 + adaptation du banc | Compteurs cohérents ; vraie géométrie mesurée, référence visuelle fiable. |
| B | C3 + C5 + C6 | Listes/filtres sans débordement, virtualisation conservée. |
| C | C4 + C7 + contrats d’actions Accueil | Erreurs distinctes du vide, repli Numériques → Tous, notifications contextualisées. |
| D | Plugin Accueil + C8 + C9 accepté + C10 | Actions ciblées, paramètres accessibles, état système explicite. |

Point 20 : hors livraison tant que non validé. Refonte local/en ligne : backlog de fond.

**Banc à adapter, sans changer les fixtures pour cacher C1 :** `datasetctl.py:command_snapshot`, `profiles/ux-small.toml`, harnais `headless.py/capture_app.py`. Distinguer résolution du moniteur, taille demandée et taille obtenue. Xvfb seul n’applique pas nécessairement une demande plein écran : fournir un gestionnaire de fenêtres compatible et vérifier l’état GTK/GDK ainsi que la géométrie native. Ne pas présenter une simple capture 1 366 × 768 comme preuve de plein écran.

Recette principale **1 366 × 768 plein écran**, secondaire **largeur 1 024**, mêmes données et échelle. Activer explicitement `core.ui.pages`, `core.ui.widgets`, `core.ui.titlebar`, `core.ui.system` et les providers utiles : `ux-small` ne force initialement que l’Accueil en plus des providers. Le validateur `ensure_required_plugins()` inspecte le store externe ; lui apprendre à reconnaître les plugins intégrés au Core au lieu de le contourner.

Pour chaque lot : tests métier ciblés, vérification GTK des allocations et capture des écrans concernés après un signal de chargement terminé ou une erreur explicite, avec timeout borné. Enregistrer versions, identité, filtres, géométrie, état plein écran et résultats par source. Conserver des fixtures avec identités explicites **et** des fichiers non attribués, du contenu très long et des erreurs partielles. Aucun gel de l’interface ni travail réseau sur le thread GTK.
