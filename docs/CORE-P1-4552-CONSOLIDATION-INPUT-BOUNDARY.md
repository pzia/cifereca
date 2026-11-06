# P1 #4552 — admission bornée des preuves descriptives

## Contrat d'entrée, sans moteur métier

`src/cifereca/core/notice_consolidation_inputs.py` fournit `admit_descriptive_facts` en pur Python, **sans persistance, HTTP, plugin ni compte**.

Le Core accepte un **type canonique libre**, un consolidateur explicitement identifié et versionné, une **édition cible exacte**, des faits source descriptifs et une table de **relations fortes déjà attestées en amont par le Core**. Ne jamais créer une relation en comparant titre/auteur, ISBN porté par une autre manifestation ou similitude de métadonnées.

Deux catégories distinctes : `exact` (même édition) et `inherited` (édition étrangère reliée par une preuve forte et explicite) ; chaque fait hérité conserve `edition_id`, `relation_proof_id`, `source_revision` et `inherited=True`. Un simple lien fort **n'autorise pas à lui seul l'héritage effectif du type** : le plugin de consolidation sélectionné, par type et portée, décide selon son contrat métier si l'héritage de la **description** est licite. Le Core ne contient aucun réducteur spécifique à l'âge/genre/série.

Règles : maximum 512 faits et 128 relations par appel, valeur brute ≤ 2048 octets, identifiants opaques bornés, dédoublonnage idempotent par `source_id, edition_id, evidence_id`, conflit de révisions ou contenu → échec global, ordre source déterministe. Aucune donnée privée surnuméraire d'une ligne n'est exportée.

## Non-objectifs — indispensables avant livraison du système C

- **Aucun droit, permission, licence, quota, offre ou identité personnelle** ne passe dans les résultats canoniques ; ce helper n'est autorisé que sur la voie déjà validée de *preuves descriptives*, jamais sur des objets fournisseurs bruts ou droits.
- Le mapping métier, normalisation des valeurs, règles d'héritage, quorum, choix explicite du plugin et arbitrages de conflits entre **preuves distinctes** ne sont **pas** effectués ici.
- Aucune promotion automatique de `inherited` vers un fait canonique. L'état inconnu reste inconnu tant qu'un consolidateur validé ne fournit pas de résultat.
- Ne pas brancher ce helper sur le catalogue ni annoncer #4552 terminé avant le registre typé, les contrats d'ABI, le cache de parsing et la consolidation asynchrone.
