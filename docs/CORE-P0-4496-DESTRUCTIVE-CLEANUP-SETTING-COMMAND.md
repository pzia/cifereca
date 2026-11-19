# #4496 — Commande de réglage (tranche Core)

Le réglage est local, global à l'installation et indépendant du profil
sélectionné. La commande privée ne supprime rien, ne reçoit aucun plan
et n'est pas exposée comme broker plugin. Confirmation explicite, valeur
booléenne stricte, vérification de la configuration attendue et verrou
d'acquisition commun. Si le verrou est occupé, refuser et inviter à
réessayer : ne pas promettre une interruption d'un unlink déjà engagé.

L'activation enregistre d'abord une intention dans action_events
(action fixe, request_id aléatoire, deux booléens), puis sauvegarde la
configuration et journalise l'application. Si l'audit initial échoue,
refuser l'activation ; la désactivation demeure possible avec signalement
de journal incomplet. Pas de fausse atomicité JSON/SQLite : un événement
requested sans applied exige une réconciliation, pas une inférence.

La sauvegarde générale des préférences préserve la valeur Core courante
de cette clé au lieu d'adopter celle d'un brouillon ancien. L'interface
avancée et son consentement dédié sont un lot distinct ; le contrôle
des éditions externes/ABA et les garanties de durabilité face à une
panne électrique ne sont pas établis par cette tranche.
