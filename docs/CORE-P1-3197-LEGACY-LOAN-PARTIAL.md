# Core P1 #3197 — retrait du signal mutable de complétude v1

Les lectures historiques `session.loans(account)` conservent les prêts
**positivement observés**, mais ne fournissent aucune attestation atomique de
pagination ni de branches. `session.last_loan_errors` peut changer entre deux
lectures concurrentes et ne prouve **jamais** qu'un inventaire est complet.

Les deux consommateurs Core (synchronisation de source explicite, synchronisation
à l'ouverture) attribuent désormais un statut de source **partial** à toute
lecture legacy v1 ; un seul événement fermé `loan_partial_failure` est
comptabilisé par source et aucun contenu des erreurs fournisseur n'est lu.
Un snapshot typé `success` garde son statut; `partial` reste `partial` et
`failed/cancelled` reste rejeté avant publication.

Cela **ne retire pas l'API legacy** `loans(account)`, ne modifie pas ses
lectures fournisseur, ne déclenche aucun GET supplémentaire et ne crée aucune
preuve d'absence, de restitution ou de suppression de fichier.

Restant : dépréciation versionnée des contrats v1, remplacement du chemin
`_loans_for_account` par l'adaptateur typed pour tous les producteurs
conformes, disparition du canal mutable des plugins et recette conformance
multi-comptes #3122. Aucune modification de DRM ni release.
