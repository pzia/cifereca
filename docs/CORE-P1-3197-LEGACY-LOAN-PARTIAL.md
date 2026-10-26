# Core P1 #3197 — retrait du signal mutable de complétude v1

Les lectures historiques `session.loans(account)` conservent les prêts
**positivement observés**, mais ne fournissent aucune attestation atomique de
pagination ni de branches. `session.last_loan_errors` peut changer entre deux
lectures concurrentes et ne prouve **jamais** qu'un inventaire est complet.

Les deux consommateurs Core n'utilisent plus ce canal mutable comme preuve.
La synchronisation **explicite** marque une lecture v1 `partial` pour en
signaler la couverture inconnue. La synchronisation **d'ouverture** garde le
statut opérationnel historique `success` lorsqu'une lecture v1 réussit,
pour ne pas transformer en erreurs les comptages ni les autres sources d'une
même identité. Ce statut opérationnel **ne signifie jamais couverture complète**:
la réconciliation négative reste interdite sans snapshot typé et branches
explicitement attestées.

La lecture typée `partial` produit un seul marqueur fermé
`loan_partial_failure` par source ; `failed/cancelled` reste rejeté
avant publication. Les erreurs mutables legacy ne sont jamais inspectées.

Cela **ne retire pas l'API legacy** `loans(account)`, ne modifie pas ses
lectures fournisseur, ne déclenche aucun GET supplémentaire et ne crée aucune
preuve d'absence, de restitution ou de suppression de fichier.

Restant : dépréciation versionnée des contrats v1, remplacement du chemin
`_loans_for_account` par l'adaptateur typed pour tous les producteurs
conformes, disparition du canal mutable des plugins et recette conformance
multi-comptes #3122. Aucune modification de DRM ni release.
