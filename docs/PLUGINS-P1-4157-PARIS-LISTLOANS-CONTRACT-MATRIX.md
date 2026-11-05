# #4157 — couverture de lecture ListLoans Paris

## Autorité et portée

L’arbitrage utilisateur du 02/11/2026 considère la consultation officielle
ListLoans réussie comme la liste courante complète du **compte exact et de
la branche demandée**, y compris `Loans=[]`. Aucun indicateur de pagination
supplémentaire n’est exigé sur la réponse nominale de cette API.

Le client exige HTTP réussi sans redirection, JSON `success=true`, enveloppe
`d.Loans` sous forme de liste de dictionnaires. Les indicateurs explicites de
pagination inachevée et les totaux contradictoires sont refusés (#4474).
Le lecteur conserve ses gardes identité/credential/emprunteur/instance de compte
et sa sérialisation avec connexion et fermeture ; aucun cache ni GET n’est ajouté.

## Certificat par branche

`_snapshot_for_account` qualifie séparément `dilicom/digital` et
`syracuse/physical`. Une branche lue avec succès porte `complete` et
`pagination_complete=True` si toutes ses lignes ont des identifiants amont
`Id`/`HoldingId` et `RecordId` valides, correspondent au Loan normalisé et ne
répètent pas le même identifiant de prêt. Une liste vide valide est complète.

| Scénario | Couverture et résultat |
| --- | --- |
| Deux branches nominales, 0/1/N prêts | Chaque branche `complete`, snapshot `success` |
| Erreur HTTP/auth, timeout, HTML, redirection, forme/pagination invalide | Branche `partial/branch_unavailable`, snapshot `partial` ; certificat propre de la branche réussie conservé |
| Identifiants manquants/invalides, même prêt répété ou contradictoire | Branche `partial/invalid_response`, snapshot `partial` ; lignes positives/descriptives conservées, aucune absence certifiée |
| Client legacy fournissant des Loan sans preuve brute | Branche `unknown`, aucun certificat de complétude fabriqué |
| Scope étranger, compte remplacé, credential/session révoqué | Refus ; pas de deuxième GET privé sous un scope changé |
| Nouvelle lecture vide après une lecture non vide | Nouvelle observation indépendante, sans réutilisation des lignes/preuves précédentes |

Le helper public `branch_claims_complete` conserve sa garde Core : un
snapshot globalement `partial` n’autorise aucune clôture par absence, même
si sa branche sœur porte un certificat positif. Cette PR ne modifie pas le Core.

## Lecture, livraison et conservation

La couverture décrit la **consultation distante**, jamais la disponibilité
locale d’un EPUB/PDF, la réussite ADEPT, le consentement ou un droit à une
nouvelle action. Un fichier manquant ou une acquisition échouée conserve son
état `partial/failed`. Aucune restitution volontaire, suppression de fichier
ou mutation fournisseur n’est effectuée par les adaptateurs d’inventaire.

Les tests d’intégration utilisent le vrai adaptateur Paris sur
`httpx.MockTransport`, puis la Sync Core et SQLite/FS réels :

- liste non vide puis zéro valide : seul le prêt numérique du borrower exact
  est terminé ; l’autre borrower reste actif ; Placements, origines fournisseur
  et ADEPT et octets sont conservés ; un prêt réobservé redevient actif ;
- une branche indisponible ne permet aucune fausse clôture ;
- source correctement lue avec fichier exact attesté : résultat nominal ;
  fichier manquant ou branche échouée : résultat partiel, sans acquisition.

Suites : `src/plugins/provider-paris/tests/test_p1_4157_listloans_completeness_matrix.py`
et `tests/test_p1_4157_paris_complete_inventory_core.py`, plus les gardes
existantes #3222/#3252/#3723 et le gate de conformance Paris/BnF.
Les trois anciens `xfail` d’acceptation positive deviennent des assertions
actives ; les autres `xfail` documentaires restent indépendants.

Les réponses HTTP et données des tests sont synthétiques. Les recettes réelles
consignées dans #4157 et #3723 attestent des lectures nominales distinctes
(2 DILICOM + 12 SYRACUSE pour une portée, zéro pour une autre), sans prouver à
elles seules l’ancienne valeur interne du certificat. Aucun nouvel emprunt,
retour, fulfillment ADEPT ou capture n’est requis pour cette livraison technique.
Les optimisations de relecture/cache restent #4281 et le cas BnF réellement
sans prêt reste #4480 P2/PU.
