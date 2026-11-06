# P1 #4552 — Contrôle du résultat d'un consolidateur descriptif

## Frontière et ordre des étapes

L'entrée `admit_descriptive_facts` (#4724) contient les preuves `exact` et `inherited` avec leur provenance. La sélection déclarative `select_consolidator` (#4727) retourne un plugin/version/type/portée et un `schema_id` explicites. Cette tranche introduit `validate_descriptive_result`, une **validation pure de la projection proposée**, sans appeler le plugin ni écrire un résultat C.

Le worker futur devra : vérifier le manifeste/plugin chargé, sélectionner le bon consolidateur pour la portée, vérifier son schéma métier déclaré et ses limites d'exécution, exécuter dans une isolation adaptée, puis appliquer cette validation de contexte/provenance **avant** toute transaction SQLite. Le runner doit également confirmer que la portée de la sélection reste actuelle.

## Contrat de sortie proposé (fermé)

Le plugin peut retourner `None` (aucune preuve canonique, donc inconnu) ou un objet avec **exactement** :
- `canonical_type`, `target_edition_id`, `schema_id` : correspondance stricte à la sélection et à la cible.
- `value` : texte descriptif UTF-8, non vide, ≤ 2 KiB (sémantique vérifiée par le schéma métier du plugin).
- `inherited` : vrai booléen, jamais implicite.
- `source_evidence_keys` : liste non vide de **triplets structurés** `[source_id, edition_id, evidence_id]` (maximum 128), sans doublon. Les triplets doivent tous exister dans les preuves de la catégorie `exact` ou `inherited` choisie. Pas de clés concaténées au séparateur ambigu.

**Priorité conservatrice :** si des preuves descriptives exactes existent, le résultat ne peut pas s'appuyer sur des preuves héritées ; le moteur métier reste responsable des conflits entre sources exactes et de la possibilité d'hériter en l'absence de preuve exacte. Aucune transformation d'un droit/offer/credential/compte ne doit entrer sur ce chemin : toutes les clés inconnues sont refusées. Le validateur ne remplace **pas** le contrôle métier d'un `schema_id` par le futur registre.

En cas de cible/version/type/schema incohérent, référence sans attestation, répétition, Unicode invalide ou dépassement, l'opération est refusée entièrement ; aucun écrasement d'une notice C existante n'est induit. Les tests n'utilisent que des fixtures descriptives hors réseau et sans SQLite.

## Non-objectifs

Aucune normalisation d'âge, genre, langue, série, aucun choix de plugin par ordre, aucun GET, aucune permission de prêt, aucune persistance ni worker asynchrone. #4552 reste P1 ouvert jusqu'au registre ABI, à la persistance révisée, au scheduling, à l'invalidation et aux essais multi-plugins réels.
