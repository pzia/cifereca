# P0 #4858 — D4 : consentement on_start du primaire Kobo vide

Le Source ID historique de `source_materialization_schedule` distingue
une source par identité/provider/credential et un emprunteur Kobo normalisé.
Un `Account.unique_id=""` y est la valeur canonique **du compte primaire**,
mais le contrôle `_configured_binding` acceptait aussi un compte
`is_primary=False` avec cet identifiant vide. Il était possible de
réutiliser le même ID et l'ancien ON `on_start` du primaire après un
remplacement de compte dans le Core ou la session.

La décision de programmation refuse désormais cette valeur vide sauf si
**les deux représentations**, compte Core et compte vivant de la session
fournisseur, ont `is_primary is True`. Les emprunteurs non vides exacts
continuent d'utiliser leur consentement historique ; l'étiquette Kobo-Vault
`identity_id="default"` demeure admise uniquement sous les contrôles
d'identité SQL actuels.

Aucun GET ni horloge automatique n'est déclenché sur une réaffectation
secondaire ; la préférence v0.7 `on_start` persiste pour diagnostic et
rollback, sans être interprétée comme une autorisation nouvelle. Le
consentement CAS canonique reste une barrière indépendante.

Recette : `tests/test_core_p0_4858_d4_empty_secondary_onstart.py`.
Deux permutations Core/session et nouvelle tentative d'opt-in secondaire :
aucune décision d'acquisition, aucun avancement du clock, retour à l'owner
primaire uniquement avec liaison authentifiée.

Ce contrôle est négatif et ne vaut pas migration des sessions W2 ABI partielle,
ni retrait global du planificateur D4 historique.
