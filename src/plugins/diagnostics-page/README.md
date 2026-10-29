## 0.1.28 — Export ZIP local du seul jour courant (#3649)

Dans **Diagnostics → Journaux**, l'action principale « Exporter le journal
brut du jour (ZIP)… » ouvre après confirmation un sélecteur GTK4 SAVE. Le Core
produit une archive locale privée à la date du jour en fuseau IANA, sans
filtre d'écran ni masquage, avec JSON des événements, manifeste SHA-256, bornes
UTC réelles (DST) et limites de rotation explicites. Permissions 0600,
publication exclusive sans écrasement, annulation sans ZIP partiel ; aucune
lecture fournisseur ni transfert réseau. L'ancien export de toutes les
rotations JSON est distinct et placé sous Filtres avancés. Un Core plus ancien
ne présente pas l'action ZIP comme fonctionnelle.

Tests déterministes Core, UI sans GTK et GTK4 réel (sous DISPLAY).
Voir `docs/DIAGNOSTICS_CANONICAL_JOURNAL.md`.

## 0.1.20 — Fournisseur attesté et filtre exact (#2900)

Le champ « Fournisseur » filtre les événements avant pagination, séparément
du module et du canal : utiliser l'identifiant canonique, par exemple
`paris`, `kobo` ou `numilog`. Une valeur vide
conserve tous les événements. Ce filtre demande le contrat Core de #2902 ;
un Core antérieur ne reçoit pas de repli silencieux sur une recherche locale.

Le fournisseur publié par le Core est visible, recherchable sur la page et
conservé dans les copies, sélections multi-pages et preuves de l'aperçu IA.
Un aperçu IA ou une sélection multi-page hors fournisseur demandé est refusé.
Les données privées malformées sont omises ; aucun provider n'est déduit du
module, du message ou du runtime ADEPT. Les sites et identifiants publics de
livres restent une tranche ultérieure ; aucune nouvelle collecte réseau.

## 0.1.19 — Durée du contrôle des chemins enregistrés (#2730)

Lorsque le Core #2759 émet la mesure fermée `phase_ms.stored_paths`, les
Journaux de Diagnostics affichent « chemins enregistrés : N ms » dans la
carte de réconciliation et l'export d'agrégats. Ce nombre exprime le temps
du contrôle local de placements, **pas** le nombre de livres réellement
absents. Une valeur non mesurée reste absente, jamais `0` inventé.

Ni les chemins contrôlés ni les comptes, titres, ISBN ou SHA ne sont
recopiés. Le diagnostic reste expurgé, borné et en lecture seule.

## 0.1.18 — Export expurgé des agrégats de réconciliation (#2730)

L'export d'événements sélectionnés comporte un objet `reconciliation`
**uniquement** pour les événements terminaux Core validés de catégorie
`LIBRARY`. Il inclut statut et causes sous codes fermés, compteurs
d'inventaire et de catalogue, durées de phases (dont SQLite/WAL) et durée
totale mesurée. Ces données sont **reconstruites et revalidées** avant la copie
ou l'export multi-page ; aucune preuve individuelle n'est exportée.

Titres, auteurs, identités, comptes, chemins, ISBN, empreintes, corps EPUB,
lignes SQLite et texte d'erreur restent interdits. Un zéro de candidat sur
une analyse partielle ne vaut **jamais** preuve d'absence. Les plafonds
existants (50 événements/24 Ko par page ; 200/96 Ko en sélection avancée)
restent stricts : en cas de dépassement l'export est refusé, jamais tronqué.

## 0.1.17 — Durée de lecture SQLite/WAL (#2730)

Les événements de réconciliation issus du broker Core #2750 peuvent afficher
« **Lecture SQLite/WAL : N ms** », y compris lorsque la lecture échoue avant
le premier fichier. Cette mesure bornée décrit le temps écoulé, **pas**
l'exhaustivité, la validité ou l'absence d'un droit. Si le Core ne l'a pas
mesurée, le champ est absent : zéro milliseconde n'est jamais inventé.

La valeur est un entier non négatif issu du contrat Diagnostics attesté,
jamais un chemin local, une ligne SQLite, un compte ou du texte d'exception.
Les durées SHA/OPF/structure restent affichées séparément.

## 0.1.16 — Filtrer globalement les causes de réconciliation (#2730)

Dans **Diagnostics → Journaux**, le champ « Limite de réconciliation » accepte
un **code Core exact** : par exemple `time_budget` ou
`metadata_candidate_time_budget`. Valider avec **Entrée**. À la différence
de « Rechercher dans cette page », le filtre est appliqué **par le Core
avant la pagination** et retrouve aussi les causes secondaires
`detail.reasons`. Il respecte source, module, période et gravité.

La sélection multi-page réutilise exactement ce filtre à chaque lecture
et refuse un broker renvoyant un événement hors code, plutôt que d’exporter
une sélection trompeuse. Les exports demeurent expurgés et bornés. Un
ancien Core qui ne connaît pas `reason_code` ne reçoit **aucun repli
silencieux** vers une recherche locale : le filtre est indisponible.
Le Core doit inclure le contrat de la PR #2746 pour activer ce parcours.

## 0.1.15 — Réconciliation dans les Journaux (#2730)

Les événements Core `LIBRARY / library_reconciliation_audit` (issus du broker
`diagnostics.events`) présentent désormais les **fichiers commencés,
observés, SHA vérifiés**, les volumes de notices/références examinées et les
temps cumulés SHA, OPF, structure et comparaison. Les causes explicites sont
visibles avec leur code, retrouvable dans la recherche de la **page courante
(50 événements maximum)**, tout en conservant les filtres serveur module,
gravité et période. Un inventaire partiel — même avec zéro candidat — ne
prouve aucune absence de livre ou de droit. Champs hors allowlist, identités,
titres, ISBN, fichiers, empreintes et exceptions ne sont pas rendus ni exportés.
Le plugin n’effectue aucun réaudit, mutation, transfert ou accès réseau.



## 0.1.12 — Journaux et capture dans Diagnostics (#2139)

Journaux est l’onglet d’arrivée. Capture regroupe démarrer, arrêter,
actualiser et enregistrer les mesures JSON locales ; État système et
Rapports et vérifications gardent les autres outils. Une lecture de Capture
ne démarre rien. La capture Core partagée continue à la navigation ; seule
l’action Arrêter ou la fermeture du Core la termine. Un ancien Core sans
UiPerformanceHost indique son indisponibilité, sans import privé.
L’export explicite utilise le même sélecteur local et l’écriture asynchrone
privée que le rapport support ; aucun envoi réseau. Les préférences et le
repli Core des Paramètres sont une tranche Core distincte.
## 0.1.13 — Tâches liées sur la page de journaux (#2380)

« Regrouper par tâche liée (cette page) » rassemble seulement les observations
portant une même `task_ref` explicite du broker Core. Les références absentes,
contradictoires ou malformées affichent « Corrélation non disponible » ; même
heure, message ou identifiant d'événement ne prouvent aucun lien. Les lignes
Core et journal restent distinctes, avec leurs propres statuts et détails.
Le groupe n'est ni un bilan de réussite ni une preuve de causalité.

La portée reste la page chargée, au plus 50 événements. Pagination, filtres,
rétention et rotation peuvent cacher des étapes ; le regroupement ne lance
aucune nouvelle lecture, tâche ou collecte. La copie explicite conserve la
référence validée et les plafonds de sélection existants ; aucun contenu
brut, compte ou champ arbitraire supplémentaire n'est exporté. L'IA conserve
sa frontière et son consentement distincts. La référence éphémère permet de
relier des activités : considérer ce lien avant de transmettre un export.
