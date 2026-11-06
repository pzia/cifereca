# P1 #4552 — schéma textuel canonique déclaré par plugin (tranche isolée)

## Portée

Le Core peut désormais valider **un schéma textuel simple** déclaré par un
plugin, sans codage spécifique aux types `age`, `genre`, `series`,
`language` ou aux futurs types personnalisés. C'est une première ABI de
validation **purement locale**, pas un registre persistant, un runner, un
parser, une consolidation ni la capacité de déclencher un GET.

`CanonicalTextSchema` associe exactement `canonical_type`, `scope`,
`plugin_id`, `plugin_version`, `schema_id` au `ExplicitSelection` existant.
`select_text_schema` refuse tout registre mal formé, les déclarations
dupliquées même identiques, et n'effectue jamais de choix opportuniste.
Une sélection déclarée mais non installée retourne `None` (inconnu).

Le contrat descriptif actuel est **texte UTF-8 non vide uniquement** :
- longueur maximale déclarée, bornée à 2 KiB ;
- ensemble énuméré facultatif (au maximum 128 valeurs explicites, sans doublon) ;
- `inherited_allowed=False` pour interdire une proposition héritée.

La propriété `inherited_allowed=True` **ne suffit jamais à autoriser un
héritage** : les relations fortes, l'édition exacte et les preuves sont
vérifiées séparément par `admit_descriptive_facts` et
`validate_descriptive_result`. Le plugin métier effectif reste responsable
de la sémantique de la valeur, sans donner le moindre droit fournisseur.

## Appel prévu

1. Authentifier le plugin/manifest et la portée avec la future couche Core.
2. Résoudre un `ExplicitSelection` unique, versionné.
3. Valider et sélectionner le schéma de texte de **ce même** plugin/version/type/portée.
4. Exécuter le consolidateur métier isolé, sans réseau ; valider l'entrée,
   la preuve exacte/héritée, puis la sortie `ValidatedDescriptiveResult`.
5. Appliquer `validate_text_schema_result(result, schema=..., selection=...)`
   avec **le même** `ExplicitSelection` que les étapes 2–3. Le contrôle
   compare également `scope` : un schéma d'une autre portée, même avec le
   même `schema_id`, ne peut élargir les valeurs ou autoriser l'héritage.
   Cette fonction n'écrit ni cache ni SQLite.

**Règle de cache/invalidation** : le fournisseur du type DOIT changer
`schema_id` dès qu'il change ses contraintes (énumération, taille,
héritage) ; la clé de lineage #4735 inclut déjà cet identifiant. Sans
attestation de cette version, aucun résultat obsolète ne doit être promu.

## Non couvert

Les valeurs numériques, tableaux, structures JSON et validation de schémas
enrichis exigent une autre ABI déclarée, pas une conversion tacite vers du
texte. Il reste à livrer le registre Core authentifié/persisté, le chargement
du plugin, l'orchestrateur asynchrone, le stockage source et consolidé, et le
test GTK. Aucune table A/B, permission, prêt, acquisition ou source réelle
n'est touchée dans cette tranche.
