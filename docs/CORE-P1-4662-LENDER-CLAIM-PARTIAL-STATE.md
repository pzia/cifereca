# P1 #4662 — afficher le prêt du prêteur sans inventer un transfert

Cette tranche fournit `LibraryService.cross_profile_borrow_progress(requesting_identity_id, borrowing_account, exact_notice)`. Elle **ne crée aucun checkpoint supplémentaire** : elle lit uniquement le claim durable `loan.borrow.v1:` du mécanisme normal #3121, recalculé avec `borrow_claim_key` pour un prêteur et une édition/source/collection exacts. Les identités demandeuse et prêteuse sont distinctes et doivent être connues ; la connexion SQLite doit appartenir au prêteur et l'objet `Account` doit encore être enregistré une seule fois.

## Sémantique conservatrice

| Reçu fournisseur Core | Prêt | Livraison | Transfert | Emprunter de nouveau |
| --- | --- | --- | --- | --- |
| Aucun checkpoint | `not_attempted` | `unavailable` | `not_attempted` | Non, pas par cette API |
| `confirmed` + `request_accepted=true` + `positive_active_loan` + ID exact | `confirmed` | `unavailable` | `blocked` | **Jamais** |
| `request_sent/submitted/verification_unavailable/blocked`, checkpoint corrompu | `unknown` | `unavailable` | `blocked` | **Jamais** |
| Compte, identité ou source non attestés | `unknown` | `unavailable` | `blocked` | **Jamais** |

Le reçu de prêt chez A n'atteste **ni fichier utilisable**, ni droit de lecture chez B, ni copie indépendante, ni transfert de licence. Même après un prêt confirmé, le transfert reste `blocked` tant qu'aucune preuve légale et technique indépendante n'est fournie. L'absence de fichier ACSM n'annule pas l'emprunt A. Le Core ne réarme jamais le POST après timeout ou après échec de livraison.

La sortie `CrossProfileBorrowProgress` ne comporte **aucune donnée fournisseur privée** : ni compte, ni URL signée, ni numéro de prêt, ni opération. Elle est constamment `executable=False` et `retry_borrow=False`. Aucun fournisseur n'est appelé, aucune copie n'est faite, aucune restauration de droit n'est publiée chez B.

## Révocation de la connexion prêteuse (#4662)

La propriété de la connexion SQLite ne suffit pas à rendre son reçu privé
consultable depuis un autre profil : le compte prêteur peut avoir été
**désactivé sans suppression de son historique de prêts**.

Avant **toute lecture** du `loan.borrow.v1:`, puis **après** lecture des
deux checkpoints, le Core exige que le **profil demandeur reste le profil
actif**, une seule connexion enregistrée pour
`(identity_id, provider_id, credential_id)`, `enabled=True`, et une
propriété/restauration de l'objet `Account` inchangées. Une liste ambiguë,
malformée, réaffectée ou désactivée rend uniquement le code fermé
`lender_scope_unverified` (`loan=unknown`, `transfer=blocked`).
Cela ne réarme jamais l'emprunt, ne purge pas le reçu historique et ne
considère pas les catalogues communs comme des prêts partagés.

Les tests contrôlent le refus **avant consultation du reçu** sur connexion
désactivée/absente/dupliquée, le refus si la connexion devient désactivée
pendant la lecture des checkpoints, et la reprise en lecture seule après
réactivation du compte exact. Aucun accès fournisseur ni mutation de fichier.

## Frontière et recette

Cette projection ne vaut **pas** exécution inter-profils, et n'appelle jamais `materialize_to_identity`. Les tests Core+SQLite `tests/test_core_p1_4662_lender_claim_progress.py` couvrent prêt confirmé, état ambigu, claim corrompu, reprise interdite, source étrangère, compte réaffecté et éditions/collections voisines.

L'intégration future du parcours #4662 utilisera cette distinction entre `loan` et `transfer` après un véritable prêt sous le profil prêteur. Elle devra encore prouver la livraison, l'usage sous licence, le consentement et la copie idempotente ; aucun de ces droits n'est accordé ici.
