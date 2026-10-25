# Core P1 #3121 — prolongation Paris sûre, explicite et non répétable

## Défaillance corrigée

Le parcours `LibraryService.renew(loan)` acceptait un prêt issu d'un
cache/UI avec `raw` absent, relisait le fournisseur, puis prenait la
**première ligne du même ID** sans prouver ni la branche
DILICOM/SYRACUSE ni l'édition, la credential ou l'emprunteur. Une réponse
du POST `RenewLoans` était affichée avec « Prêt prolongé » bien qu'aucune
nouvelle échéance n'ait été confirmée.

## Contrat effectif

- La commande devient `renew(loan, *, confirmed=False)` : seule
  `confirmed is True` ouvre le parcours. La boîte GTK fige le prêt choisi
  et l'epoch Core avant confirmation. Les appels historiques sans accord
  reçoivent une réponse `blocked` sans réseau.
- Paris uniquement : source/provider/credential/borrower exacts, compte
  connecté, `renew_loan=True`, session/enregistrement Core/identité/epoch
  contrôlés via `LoanActionScopeLease`. Tout changement d'identité,
  y compris A→B→A, invalide la tentative.
- Une seule lecture fraîche `session.loans(account)` pour le **même
  emprunteur**. La permission exige exactement une ligne du même ID,
  branche, support, édition et source, `renewable=True`, et un JSON Paris
  positif `CanRenew=True` avec l'ID effectivement envoyé. Ni le cache
  `Loan.raw` ni un ID partagé entre deux branches n'autorisent un POST.
  Un prêt numérique et un prêt physique restent des opérations séparées.
- Revalidation de la source avant le POST et après la réponse. Un changement
  pendant l'appel ne peut jamais produire une confirmation.
- Verrou processus existant des mutations d'un prêt (commun à ADEPT/retour
  Paris), plus **claim SQLite atomique avant RenewLoans**, indexé par SHA-256
  de la provenance exacte (pas de titre, compte, identifiant ni URL en clair).
  Un second clic, une reprise et un timeout **ne renvoient jamais de POST**.
  Aucune réouverture automatique d'un claim, même en cas de reçu corrompu.
- La réponse fournisseur `Renewed=True` / `success=True` n'est qu'un
  résultat `submitted`. L'IHM affiche « demande transmise, échéance non
  confirmée » ; elle n'affiche plus de succès définitif avant preuve
  d'une nouvelle échéance dans un inventaire distinct. L'absence de preuve
  et les timeouts renvoient `verification_unavailable`. La lecture
  ultérieure peut actualiser l'écran, mais jamais déclencher un nouveau POST.

## Frontières et compatibilité

Aucune mutation d'entitlement, suppression locale, publication, ADEPT,
ACSM, nouvel emprunt, ni retour fournisseur. L'action existante reste
expérimentale dans le panneau de prêt ; aucune capacité publique
`lending.renew` n'est ajoutée au manifeste Paris lors de cette tranche.
Cela évite de promouvoir une fonction en simple effet du code Python ;
la migration public/legacy #3120 et les décisions d'accès #3121 restent à
intégrer séparément. BnF Prêt et les sources non-prêteuses demeurent
`unsupported/blocked` dans ce nouveau chemin.

Le reçu SQLite de demande de prolongation est distinct du reçu de retour
PNB et ne vaut **ni droit**, **ni attestation de date renouvelée**.
Un nouveau cycle de renouvellement devra s'appuyer sur une preuve positive
d'un nouveau prêt avant de lever explicitement cet anti-replay ;
ce mécanisme n'est pas ajouté sans recette fournisseur.

## Preuves synthétiques

`tests/test_core_p1_3121_paris_renewal_one_post.py` : accord strict,
source exacte positive, branches dupliquées, autre credential/édition,
permission non attestée, timeout et redémarrage SQLite, réception POST
ambiguë, mutation session/identité/epoch pendant lecture ou POST, concurrence
entre workers et rendu IHM sans faux succès. Les faux comptes ne contactent
aucun service réel.

Restent #3121 : adoption du contrat `lending.renew` statique dans une
version ultérieure du manifeste, qualification d'une nouvelle échéance,
consentement/autorisations des autres actions, recette fournisseur réelle
Paris et tests GTK d'acceptation. Aucune release.
