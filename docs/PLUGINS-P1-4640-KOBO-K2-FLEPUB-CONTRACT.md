# P1 #4640 — K2 : anomalie de normalisation FLEPUB, preuve fournisseur

## Contrat vérifié

Le code public KVault `src/worker.js` à la révision `6c358dc5188b527010c4625043372fe86f2a0b62` applique `normalizeKoboMediaFormat` : la valeur source `FLEPUB` donne **`media_format=epub`** et **`source_format=FLEPUB`**. `normalizeAcquisitionCandidates` copie de plus ce `raw_format` dans l'alias historique `format`. Le test KVault `tests/api-v097.test.mjs` vérifie précisément `library_sync + KDRM/FLEPUB -> epub/FLEPUB`. Sources : [worker.js](https://github.com/pzia/KVault/blob/6c358dc5188b527010c4625043372fe86f2a0b62/src/worker.js), [test v0.9.7](https://github.com/pzia/KVault/blob/6c358dc5188b527010c4625043372fe86f2a0b62/tests/api-v097.test.mjs).

Le journal utilisateur reçu pour **K2** indique `candidate_media_format=unknown`, `candidate_source_format=flepub` et `candidate_source=library_sync` ; c'est **incompatible avec cette normalisation de référence**, mais cela **ne prouve pas** la version effectivement déployée ni la cause du décalage. La correction ne doit donc **pas** accepter `unknown/flepub` dans les routes KDRM ou forcer un téléchargement.

## Nouveau diagnostic fermé

Dans l'analyse **déjà réalisée** d'une offre unique KDRM issue de `library_sync` avec `source_format=FLEPUB` :

| Attribut | Valeurs fermées | Lecture |
| --- | --- | --- |
| `candidate_kvault_flepub_contract` | `normalized_epub`, `media_unknown`, `media_other` | Indique si le média normalisé correspond au contrat de référence, est `unknown`, ou diffère. |
| `candidate_kvault_format_alias` | `matching`, `missing`, `conflicting` | Compare l'alias historique `format` à `FLEPUB` **sans émettre sa valeur**. |

La sortie est strictement expurgée. Elle ne contient ni lien signé, ni valeur brute de format inattendue, ni identifiant, ni jeton. Cette preuve diagnostique est **en lecture seule** : les contrôles d'admission et les routes `epub/flepub` restent inchangés. Les cas K1 (2/2) et K3 (2/0) conservent leurs propres compteurs.

## Interprétation ciblée

- `media_unknown + matching` : source et alias `FLEPUB` concordent, mais le média normalisé est anormal ; rechercher une dérive de version/mapping du Worker et non une incohérence du nom de format.
- `media_unknown + conflicting` : les champs fournis dans le descripteur ne concordent pas ; vérifier le producteur exact du descripteur et la version du Worker, sans deviner quelle valeur est vraie.
- `media_unknown + missing` : contrat d'alias incomplet, possible producer ancien ; ne rien normaliser localement.
- `normalized_epub + matching` : cohérent **sur ces seuls champs**, mais pas une attestation de DRM, de droit, d'intégrité de fichier ou de réussite d'exécution.

Le diagnostic est testé sur des descripteurs **synthétiques**, y compris des alias contenant des marqueurs privés, et sur le journal d'observation simulé du provider. Pour K2 réel, la version effective de KVault demeure inconnue. **Aucun PU nouveau :** le journal déjà reçu ne doit pas être demandé à nouveau.
