# P0 #3909 — reçus Core en staging sans Placement

## Lacune corrigée

L'audit `acquisition-work` couvre seulement les copies historiquement
indexées en Artifact/Placement. Après un GET Core réussi, un reçu
`canonical_receipt_staged` peut au contraire pointer vers un EPUB/PDF dans
`<library>/<identity>/.canonical-delivery/run-.../` **sans aucune entrée
Placement** : échec de promotion ou de publication, redémarrage après crash.
L'ancien audit affichait alors `not_attested` malgré la présence vérifiable
des octets. Cela ne signifie pas que les six EPUB Numilog constatés sur le
terrain ont ce format de reçu ; vérifier les données réelles.

## Nouvelle inspection, uniquement lecture

`inspect_exact_source(..., library_root=root, verify_files=True)` et le
rapport de Sync source exact examinent aussi ces reçus, sans scan global du
disque. Pour un reçu `canonical_receipt_staged`, la qualification exige
le schéma exact, une opération `_stage_id(SourceKey, offer)` cohérente,
le profil du compte, l'empreinte SHA-256, un témoin `core-delivery:` et
le namespace Core dédié. Les fichiers sont relus sans suivre de symlink ;
les preuves non lisibles ou incohérentes échouent en mode fermé.

États fermés : `core_staged_present_unpublished` (octets présents et SHA
confirmé, **aucun OriginLink exact observé**),
`core_staged_origin_recorded` (origine déjà publiée : le reçu n'indique
pas à lui seul la présence physique finale),
`core_staged_unverified` (vérification non demandée),
`core_staged_missing`, `core_staged_untrusted`.

Le compteur `staged_sha_verified_count` comprend les fichiers Core
effectivement rehashés, même sans Placement. Le compteur
`placement_reference_count` demeure zéro tant qu'aucune association
canonique n'existe. La couverture `indexed_and_core_receipts` est limitée
à 512 observations du compte ; dépassement = `truncated`.

Les exports filtrés n'émettent **jamais** chemin, nom, SHA, compte,
identifiant d'offre, contenu ou preuve de reçu. Le champ
`origin_proven_by_receipt` reste `false`.

**Aucune autorité de réparation** : l'audit n'invoque ni fournisseur,
ni ADEPT, ni promotion, ni publication ni nettoyage. Un reçu historique
n'accorde pas de droit : seule une nouvelle Sync avec offre fournisseur
positive, contexte actif, ON et garde d'identité peut réutiliser les octets
via `recover_completed_delivery`. En cas de contradiction, conserver les
octets et demander une preuve réelle, sans adoption par titre ou SHA seul.
