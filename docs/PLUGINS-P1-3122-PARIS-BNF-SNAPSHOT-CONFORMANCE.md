# #3122 — Conformité partagée des snapshots Paris / BnF Prêt (tranche 1)

**API publique utilisée :** `read_loan_inventory(session, account)` de
`cifereca.plugin_api`. Les deux providers conservent exactement la méthode
historique `loans(account) -> list[Loan]` et ajoutent facultativement
`loan_inventory_snapshot(account)`.

| Scénario testé en synthétique | Paris | BnF Prêt |
| --- | --- | --- |
| Prêts positifs, provenance du compte | `primary` ou lié exact | credential = emprunteur exact |
| Upstreams | `dilicom/digital`, `syracuse/physical` | `bnf_html/digital` |
| Une branche Paris inaccessible | autre branche conservée ; snapshot partiel | sans objet |
| Les deux branches Paris en échec | `failed` ; aucune preuve d'absence | sans objet |
| Page vide sans preuve d'exhaustivité | `unknown` par branche | `partial` systématique |
| Une page positive sans preuve d'exhaustivité | `unknown` par branche | `partial` systématique |
| Credential ou session non valides | refus de lecture | refus avant HTTP |
| Action ACSM/retour/emprunt | jamais effectuée par snapshot | jamais effectuée par snapshot |

Paris n'infère **aucune exhaustivité** depuis deux GET réussis : aucune
pagination terminale explicitement attestée au niveau du contrat legacy.
`status=success` décrit une lecture, pas une preuve de disparition.
Le champ local `Loan.identity_id` manquant dans certaines lignes Paris
est repris du compte **déjà connecté et reconnu par la session** ;
une identité explicitement contradictoire n'est jamais remplacée.

BnF reste `partial/inventory_not_exhaustive` même avec HTTP 200,
une liste vide ou un prêt positif. Le staging ACSM, la double confirmation
utilisateur, et l'activation ADEPT sont **strictement séparés**.

## Preuves et limites

`tests/test_core_p1_3122_paris_bnf_snapshots.py` exécute les véritables
classes `ParisProviderSession` et `BnfProviderSession` avec clients
synthétiques, et exige un seul appel de lecture HTML BnF pour chaque snapshot.
Aucun appel Internet, authentification réelle, token ni DRM.

**Restant #3122 :** conformance packaging/rollback, renouvellement/retour
Paris et consentements #3121, epoch/session remplacée au milieu du réseau
sur les deux producteurs, tests Core Sync et GTK ; l'UI et les transitions
des droits existants ne consomment pas encore ce contrat. Ce lot ne peut
donc pas fermer #3122, ni revendiquer une preuve d'absence.
