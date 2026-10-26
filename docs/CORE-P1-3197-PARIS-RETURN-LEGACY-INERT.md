# Core P1 #3197 — preuve legacy de retour Paris : lecture inerte

Dans les deux parcours de vérification d'une restitution numérique Paris,
le moteur cherche **d'abord** une enveloppe `loan_inventory_snapshot` typée ;
seul `snapshot.branch_claims_complete("dilicom", "digital")` peut autoriser
l'absence sur cette voie. L'ancienne branche `loans(account)` subsiste pour
les plugins de compatibilité, mais s'appuyait encore sur un
`getattr(session, "last_loan_errors")` susceptible d'exécuter un getter
fournisseur (réseau, autre compte, autre thread) au moment d'une décision
de retour.

La branche historique utilise désormais `inspect.getattr_static` :
**aucun getter, proxy dynamique ou descripteur fournisseur n'est invoqué**.
Seule une liste/tuple Python exact(e), **bornée à 64 erreurs**, peut conserver
la compatibilité SYRACUSE-only existante. Absence, propriété, payload inattendu
ou liste excessive => **refus fermé** de certifier l'absence du prêt. Les
observations positives restent admises selon le périmètre exact du compte.
Aucune consultation réseau, mutation de retour, ACSM ni ADEPT supplémentaire.

Ce lot n'érige **pas** le canal `last_loan_errors` en preuve atomique :
le risque résiduel de changement concurrent et d'absence non exhaustive
est documenté dans #3197. La suppression complète du canal legacy nécessite
une migration des fixtures et des fournisseurs vers les revendications de
pagination DILICOM, sans dégrader les protections anti-rejeu du retour.

Recette : `tests/test_core_p1_3197_paris_return_static_inventory.py`,
avec getters et `__getattr__` hostiles, séquence Python surchargée, borne
de longueur et compatibilité du cas SYRACUSE déjà couvert par les tests P0.
