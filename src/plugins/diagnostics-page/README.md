

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
