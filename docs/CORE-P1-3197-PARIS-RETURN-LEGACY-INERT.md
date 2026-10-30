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

**État au 30/10/2026 :** Paris ne produit plus `last_loan_errors`
([#3759](https://github.com/pzia/cifereca/pull/3759)). Le Core ne consulte
plus ce canal, même dans le fallback v1. Il conserve les prêts positifs du
lecteur historique, jamais une preuve d'absence ni de retour. La suppression
versionnée de l'adaptateur v1 exige encore la qualification des extensions
installées et de la compatibilité Plugin API (#3197).

Recette : `tests/test_core_p1_3197_paris_return_static_inventory.py`,
avec getters et `__getattr__` hostiles, séquence Python surchargée, borne
de longueur et compatibilité du cas SYRACUSE déjà couvert par les tests P0.
