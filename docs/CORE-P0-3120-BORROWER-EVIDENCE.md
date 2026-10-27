# Core P0 #3120 — capacité ≠ éligibilité d'un emprunteur

## Contrat livré (additif, sans endpoint)

`assess_lending_operation("loan.borrow", ...)` vérifie **seulement**
manifest, descripteur, présence de méthode, session connectée et emprunteur
connu. Son verdict `supported` n'est pas une permission d'emprunt.

`assess_borrower_operation_eligibility(capability, account, branch, evidence)`
complète cette information pour un **compte emprunteur et une branche exacts**,
sans réseau ni action distante. Les données optionnelles
`BorrowerOperationEvidence` ne peuvent venir que d'un adaptateur qui a
**réellement observé** les champs en amont pour cette opération :
`subscription` (`valid/renewal_pending/expired/unknown`),
`remaining_loans` (entier attesté, jamais extrapolé depuis des prêts partiels),
et `permission` (`allowed/denied/unknown`). Aucun parsing ou champ fournisseur
Syracuse/DILICOM n'est présumé par le Core.

- `temporarily_blocked` avec raison fermée si le bon emprunteur a
  `renewal_pending`, quota attesté à zéro ou permission explicitement refusée.
  Une souscription bloquée ne déconnecte jamais le compte et ne révoque
  aucun prêt ou droit.
- `unverified` si un champ nécessaire manque, si la branche ou le compte
  ne concordent pas, si les valeurs ne sont pas typées ou si le contrat
  statique reste incomplet. Un signal Syracuse n'est **pas** réutilisable
  pour une action DILICOM.
- `eligible_observed` uniquement lorsque capacité/statut opérationnels,
  abonnement, quota positif et permission sont tous connus **pour la même
  portée exacte**. Ce verdict est un **affichage de preuves**, pas un permis
  de lancer `borrow`. L'action finale revalide toujours compte, consentement,
  disponibilité et réponse fournisseur.

## Frontière de cette tranche

Les tests couvrent les deux cas terrain synthétiques (sous quota mais
renouvellement en attente ; compte valide mais quota atteint), deux
emprunteurs d'une même credential, branches distinctes, valeurs inconnues,
et déclarations contradictoires. **Aucun statut fournisseur n'est inventé**,
aucune méthode ni HTTP n'est appelée.

La collecte/normalisation réelle par le plugin Paris, l'éligibilité active
dans CoreUI et les vérifications de consentement restent dans #3120/#2914 ;
les clients historiques conservent leur comportement jusqu'à conformance.
Ce module seul **ne ferme pas le ticket** et n'entraîne aucune release.
