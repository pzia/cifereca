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
