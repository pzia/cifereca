# #4157 — matrice d'acceptation ListLoans Paris (préparation sans modification de l'adaptateur)

## Frontière de travail

Tant que le P0 [#3723](https://github.com/pzia/cifereca/issues/3723)
réserve les modules `provider-paris/cifereca_paris*.py`, le P1 ne
modifie **que les tests et cette documentation**. Aucun accès à un
compte réel, ni échange HTTP externe, ni appel ADEPT, ni mutation de
droit, emprunt, restitution, fichier ou base de données.

`test_p1_4157_listloans_completeness_matrix.py` simule avec
`httpx.MockTransport` les deux lectures officielles
`UserAccountService.svc/ListLoans` avec
`serviceCode=DILICOM|SYRACUSE` et le même `userUniqueIdentifier`.
Les assertions sur les emprunts positifs sont **actives dès maintenant**.

## Critères

| Scénario | Comportement exigé | État dans cette tranche |
| --- | --- | --- |
| Réponse JSON `success=true`, `d.Loans=[]` | Observation valable; 0 ligne positive; une éventuelle attestation de fin de liste doit rester *par branche et compte* | Régression active ; attestation positive **xfail explicite** |
| 1/N lignes DILICOM et SYRACUSE | Pas de fusion des deux supports, scope identité+connexion+emprunteur, exactement 2 GET | Régression active ; attestation positive **xfail explicite** |
| Échec 401, timeout, HTML, redirection, liste absente ou malformée sur une branche | `partial`; branche défaillante sans preuve d'absence; l'autre branche conserve ses seules observations positives | Régression active |
| Marqueurs `HasMore`, `NextPage`, `ContinuationToken`, `TotalCount/PageSize` non qualifiés | **Jamais** un certificat `complete` issu d'un nombre de lignes ou d'un indicateur ambigu; le futur parseur devra rejeter la pagination non résolue | Garde « pas de fausse complétude » active |
| Changement de lecteur, identité, credential, session après le premier GET | Aucun deuxième GET privé; aucun snapshot publié sous un ancien scope | Régression active |
| Compte étranger avant lecture | Refus avant tout GET | Régression active |
| Fin de liste réellement attestée par le contrat officiel pour chaque service et emprunteur | `LoanBranchCoverage(completeness="complete", pagination_complete=True)` pour **chaque branche réussie**, y compris liste vide | **Non livré : xfail explicite** |

**Point de preuve restant :** documenter/tester précisément la garantie
officielle de réponse non paginée, l'absence effective de jeton de
continuation, et les champs de compte nécessaires pour rejeter les
réponses hétérogènes. La décision produit « liste exhaustive quand
la consultation officielle réussit » ne dispense jamais le parseur
d'une forme de réponse valide.

Le statut `LoanInventorySnapshot.success` désigne la réussite de
lecture des branches, **pas** l'acquisition de fichiers ni la
convergence locale. Une erreur ADEPT/placement conserve son propre
statut `partial/failed` même avec un inventaire distant complet.
La préférence personnelle de plafond #4065 n'autorise ni nouvelle
transaction, ni calcul d'un quota serveur ou d'un « reste ».

## Suite dès déverrouillage du P0

1. Qualifier, avec le contrat effectivement exposé par le fournisseur,
   le succès terminal de `ListLoans` pour chaque couple
   `(userUniqueIdentifier, serviceCode)`.
2. Ajouter la validation stricte dans le client Paris, sans dégrader les
   protections HTTP, les conditions de réauthentification et l'isolation
   de compte. Le parseur devra préserver les détails de cause dans le
   journal privé uniquement ; aucun texte de réponse brut ne doit
   devenir un motif d'autorisation.
3. Publier la couverture positive par branche **uniquement après**
   les vérifications de réponse et de compte, tout en conservant
   `partial` pour la branche échouée.
4. Lever les `xfail` du test d'attestation positive et ajouter un
   contrôle strict de rejet des marqueurs de pagination explicitement
   non terminaux. Passer la CI plugins, les tests Core pertinents et
   l'installation/rollback sur le HEAD final, puis effectuer une
   recette réelle **sans échec ADEPT** pour vérifier l'état global.

Les fixtures sont entièrement synthétiques ; aucune pièce privée ni
capture de prêt historique n'est publiée.
