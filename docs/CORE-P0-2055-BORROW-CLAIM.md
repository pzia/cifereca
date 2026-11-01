# P0 #2055 — garde Core d'un nouvel emprunt BnF (préparation)

## Portée livrée par cette tranche

Le Core dispose de deux primitives **inertes** et indépendantes de tout provider :

- `capture_borrow_action_scope(service, notice, account, session, expected_identity_id=...)` épingle la notice (identifiant, record, docbase, medium, provider/credential), le compte objet, l'identité sélectionnée, l'epoch du service et celui du provider. Le bail revalide également l'enregistrement Core de la session, le binding persisté de la connexion et la liste de comptes stockée sans invoquer de propriété dynamique d'un plugin. L'ABA et les changements de DTO/scopes révoquent le bail.
- `borrow_claim_key(account, notice)` dérive un identifiant opaque de l'identité, connexion, emprunteur et édition exacts, stable après reconnect/restart. `Storage.loan_borrow_claim_once` réserve atomiquement ce key en SQLite (`BEGIN IMMEDIATE`, `INSERT ... ON CONFLICT DO NOTHING`) **avant** le POST. Une répétition, une erreur réseau indéterminée ou une entrée corrompue ne rouvrent jamais le claim. `loan_borrow_finish` applique un CAS sur `operation_id` ; un état `confirmed` requiert l'identifiant de prêt positivement vérifié, et ne se déduit jamais d'un 200.

Ce checkpoint **n'est pas** une preuve d'éligibilité, de consentement, de permission, de nouveau droit, d'inventaire exhaustif ni de téléchargement.

## Contrat de raccordement P0 restant

La future action CoreUI de type `loan.borrow` devra :

1. Recevoir l'accord **manuel explicite** pour la notice sélectionnée et le compte emprunteur exact (pas de Sync ni d'acquisition automatique par livre).
2. Obtenir une **permission positive fournisseur** à partir du parcours HTML frais (et de l'éligibilité réellement qualifiée, si disponible). Un bouton `.button-borrow` n'est pas cette permission ; l'inventaire partiel n'établit jamais une absence de prêt.
3. Capturer le bail avant tout I/O, le revalider après chaque lecture, et refuser les divergences de source, session, identité, notice et génération.
4. Réserver le checkpoint durable juste avant l'unique POST. En cas de timeout, redirection indéterminée ou résultat non confirmé, effectuer uniquement des lectures de vérification ; **aucun replay** implicite.
5. Utiliser la confirmation et le `BorrowForm` éphémère du provider, avec POST de champs CSRF originaux et sans suivre une redirection POST 307/308, puis contrôler une réponse positive et relire l'activité dans le même compte.
6. N'annoncer `confirmed` qu'après preuve positive du prêt `loan_id + resource_id` et scope Core toujours actuel. La vérification positive prouve le prêt actif, non sa création si la page précédente était partielle.
7. Conserver le traitement du jeton ACSM et l'import ADEPT comme une opération distincte.

**Non livré par cette tranche :** aucun POST, aucune mutation de droit, aucun bouton, aucune capacité `acquisition.borrow` publiée, aucun retry de fournisseur. Le plugin BnF reste non emprunteur jusqu'au raccordement des étapes métier et à sa qualification.

## Vérifications ciblées

`tests/test_core_p0_2055_borrow_claim.py` contrôle les scopes BnF, mutations in-place de DTO et comptes, changement de session, epoch/ABA, getter plugin hostile, isolation par compte/identité/notice, course à deux `Storage` sur le même fichier SQLite, reprise après restart, rejet des checkpoints corrompus et transitions CAS.

Ne pas confondre tests synthétiques et recette réelle BnF. Le rôle Core possède ces primitives ; Plugins possède le formulaire et l'adaptateur POST ; CI/CD valide l'installation et les tests. Refs #2055, #3121, #3118 et #4035.
