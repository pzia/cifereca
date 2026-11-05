# P0 #3121 — premier lien ACSM Paris après emprunt explicite (Core)

## Frontière corrigée

Le premier `ASSARedirect` d'un emprunt Paris peut créer le prêt et rendre
immédiatement une URL HTTPS d'ACSM. L'adaptateur Paris l'ajoute au `Loan`
indépendamment confirmé par un nouveau `ListLoans`. Historiquement
`manual_library_borrow.manual_borrow` renvoyait seulement les champs publics
fermés du prêt : cette **première offre éphémère était perdue**, même si le
claim `loan.borrow.v1` était confirmé.

Le Core retient maintenant cette offre **en mémoire de l'instance
`LibraryService`**, uniquement après `loan_borrow_finish(confirmed)`, sous
le contrat suivant :

- Prêt `DILICOM` numérique exact, identité/compte/credential Paris primaire
  et livre de la fiche admise identiques, source/session/epoch encore actifs.
- Claim SQLite déjà `confirmed`, `operation_id` et `verified_loan_id`
  égaux à ceux de l'emprunt terminé. Une offre avant vérification, un prêt
  ambigu, un timeout et une simple chaîne URL ne prouvent pas ce claim.
- URL HTTPS absolue bornée, sans contrôle/identifiant HTTP embarqué.
  **L'URL reste privée** : aucun ajout au dictionnaire de résultat du clic,
  ni SQLite, ni `Loan` d'origine modifié, ni preuve `OriginLink`.
- Maximum 12 offres, TTL de 15 minutes, consommation unique protégée par
  verrou mémoire ; revalidation exacte du scope **et du claim** lors de la
  consommation. Une édition, un prêt, une identité ou une session étrangers
  ne peuvent consommer l'offre.
- Lors de `acquire_digital_loan`, si le nouveau `Loan` authentifié n'a pas
  d'`EbookUrl`, le Core peut lui joindre **sur une copie uniquement**
  la première offre encore valide. Le plugin Paris contrôle à nouveau
  l'URL, la connexion, le compte et la staging avant tout réseau. L'offre
  ne déclenche **jamais de nouveau prêt** ni `ASSARedirect`.

## Limites explicites / suite

- **Après redémarrage**, l'URL signée n'existe plus dans la mémoire.
  Ce lot ne prétend **pas** la restaurer : la relecture du prêt existant
  reste le seul moyen de retrouver une nouvelle offre fournisseur, si elle
  est effectivement publiée. Aucun deuxième POST de prêt automatique
  n'est autorisé par ce composant.
- La récupération crash-durable et la prévention explicite d'une route
  legacy de `DownloadDigitalDocument` pouvant réappliquer le quota/une
  mutation constituent des étapes **restantes du P0 #3121**. La revue du
  fournisseur Paris sur ce point appartient au rôle Plugins.
- Pas d'ADEPT ni de publication B dans cette tranche, pas de seconde popup
  BnF (déjà supprimée par #4349/#4391), pas de changement d'API des plugins.

## Tests

`tests/test_core_p0_3121_paris_first_acsm_handoff.py` : simulateur Paris,
claim réel SQLite, premier lien ACSM unique, non divulgation, prêt/édition/
identité/credential/epoch exacts, post-redémarrage refus, suppression de
l'offre après consommation, claim altéré, et injection du lien dans le
`Loan` privé destiné au provider sans nouvel `ASSARedirect`.
