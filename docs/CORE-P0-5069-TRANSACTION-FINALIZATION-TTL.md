# P0 #5069 — fenêtre de 24 h après finalisation des transactions

Le timestamp de rétention d'une transaction terminée doit tenir compte de la dernière transition persistée par l'Updater (`updated_at`), pas uniquement de `created_at`. Une transaction créée huit jours auparavant mais finalisée il y a quelques minutes doit encore être conservée pendant les prochaines 24 h, avec ses preuves de restauration.

Le plan garde la date de création lorsqu'un reçu legacy ne comporte pas `updated_at`. Si ce champ est présent, il doit être une heure UTC canonique, non antérieure à la création ; une valeur invalide, incohérente ou inconnue rend le timestamp non qualifié et retient le journal au lieu de raccourcir le délai. Les snapshots continuent d'utiliser leur date propre mais restent référencés par leur transaction conservée.

Recette : sept transactions finalisées depuis plus de 24 h gardent au moins les deux dernières ; une transaction ancienne finalisée récemment et son snapshot restent protégés ; frontière exactement 24 h, horodatages invalides et legacy. Aucun nouveau GC destructif ; `safe_to_apply=false`.