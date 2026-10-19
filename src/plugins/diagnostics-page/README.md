

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
