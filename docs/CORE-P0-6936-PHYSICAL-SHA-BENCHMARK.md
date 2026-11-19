# #6936 — Coût du SHA-256 physique et décision de sûreté

## Autorité et inventaire des lectures

Le SHA seul ne dispense jamais d'un GET : #4496 et #3171/#3719 exigent
la référence source exacte, son origine attestée, le Placement du profil
cible et les octets frais ou un reçu durable exact. Aucune optimisation
de cette étude ne change la décision d'acquisition.

| Parcours | Lecture existante | Répétition à qualifier, non à retirer |
|---|---|---|
| Inventaire et index | `library_inventory.scan_identity_roots`, `LocalCatalog.sha256` | SHA, OPF et CRC mesurés séparément (#2565) ; réutilisation de scan sous empreinte physique, pas de preuve de droit |
| Acquisition | `canonical_selected_delivery`, `canonical_promotion`, `canonical_publication`, `canonical_recovery` | staging, copie exclusive, promotion, final, reprise ; les fichiers et moments ne sont pas toujours les mêmes |
| Présence vérifiée | `convergence_verified_presence._verified_copy` | stat + SHA frais ; jointure origine/source/identité ; pas de cache autoritatif |
| Transfert | `canonical_transfer` | original et reçu indépendants ; même SHA ne cède jamais le droit |
| Variante | `derived_variant_publication._verified_variant_file` | original et résultat, FD gardés, vérification du nom courant avant/après écriture |
| Maintenance | `canonical_dedup_preview`, `canonical_keeper_review`, `artifact_runtime.normalize_filename` | keeper revérifié par paire ; commit reprend la preuve sous verrou |
| Diagnostic | `local_bounded_hash`, `library_fresh_file_check`, `acquisition_staging_evidence` | budgets propres ; ne pas substituer une observation ancienne à une preuve de commit |
| Audit plugin | `maintenance-library-audit._file_integrity_issues` | lecture SHA + structure ; ne pas confondre audit et transaction B |

Inventaire ciblé aux parcours Publication ; hors updater, packages, signatures,
N1–N4 et autres SHA documentaires. Les lecteurs ont des conditions
différentes : le lecteur `LocalCatalog.sha256` contrôle notamment le
descripteur/nom final, le lecteur de variante ancre aussi les ancêtres
sans suivre les liens. Le benchmark ne déclare pas ces ports interchangeables.

## Reproduction

Depuis un checkout Linux installé, sans données utilisateur :

```sh
PYTHONPATH=src python scripts/benchmark_physical_sha.py --files 32 --size-mib 1 --passes 7
PYTHONPATH=src python scripts/benchmark_physical_sha.py --files 128 --size-mib 4 --passes 5
```

Corpus pseudo-aléatoire déterministe, distinct par fichier, écrit et fsync
dans un dossier temporaire créé par le script. Pas d'EPUB fonctionnel :
on mesure uniquement le coût du hash et du contrôle descripteur/nom.
Aucune DB applicative, aucun réseau, aucun cache persistant. Le comparatif
utilise la même primitive ancrée pour série/2/4 threads ; tous les octets
sont relus et le digest est comparé à chaque passe. Un recheck final
reste exigé. Les résultats ne constituent pas une preuve B ni autorisation.

Sortie : latences p50/p95/max par fichier et par passe, temps total,
CPU processus, débit logique, RSS maximal cumulatif processus, delta
`/proc/self/io` (rchar logique, read_bytes physique si disponible).
Le RSS cumulatif n'isole pas le coût de chaque configuration. L'ordre
fixe et le préchauffage favorisent le cache chaud ; pas de purge OS.

## Mesure locale exploratoire — 19/11/2026

Python 3.13.5, quota cgroup quatre CPU, environnement de calcul isolé.
Mesures sur une **reproduction locale des corps** de `LocalCatalog.sha256`,
`_verified_variant_file` et `_open_root` lus dans Git, sans checkout
installé ; les tests CI du script contre les imports réels restent
distincts. Aucun benchmark sur la bibliothèque utilisateur.

| Corpus / passes | Lecteur | Fichier p50/p95 ms | Passe p50/p95 ms | Wall ms | CPU ms | Débit Mio/s | RSS pic cumulé Kio |
|---|---|---:|---:|---:|---:|---:|---:|
| 32 × 1 Mio / 7 | catalogue série | 0,769 / 1,361 | 27,261 / 29,826 | 192,407 | 192,404 | 1164,196 | 98192 |
| idem | ancré série | 0,789 / 1,126 | 26,416 / 29,723 | 187,590 | 187,581 | 1194,094 | 98192 |
| idem | ancré ×2 | 0,864 / 1,442 | 15,870 / 21,058 | 118,150 | 218,779 | 1895,890 | 100240 |
| idem | ancré ×4 | 0,954 / 4,082 | 13,127 / 15,660 | 89,794 | 222,012 | 2494,602 | 102288 |
| 128 × 4 Mio / 5 | catalogue série | 3,163 / 5,764 | 464,889 / 494,484 | 2258,217 | 2254,984 | 1133,638 | 99212 |
| idem | ancré série | 2,765 / 3,508 | 364,614 / 419,181 | 1869,847 | 1865,718 | 1369,096 | 99212 |
| idem | ancré ×2 | 3,009 / 3,738 | 202,001 / 210,804 | 1018,608 | 1998,299 | 2513,233 | 103564 |
| idem | ancré ×4 | 3,074 / 4,282 | 108,469 / 114,423 | 545,579 | 2050,022 | 4692,265 | 107660 |

Les lectures logiques sont identiques (224 Mio puis 2560 Mio par
configuration, plus environ 100–124 octets de comptage proc). Les deltas
`read_bytes` sont tous zéro : aucun disque froid mesuré. Le gain
sur le temps total à quatre threads est d'environ 52 % puis 71 %,
mais le CPU monte, le pic mémoire cumulé augmente et la p95 par fichier
se dégrade, notamment dans le petit corpus. Pas de gain I/O logique.

## Verdict et limites

**Aucune optimisation en production dans cette tranche.** Le gain de
débit microbenchmark sur cache chaud ne démontre pas un gain sûr sur
réconciliation/maintenance sous charge réelle, disque cold, contention
SQLite/sync, annulation, processus multiples ou montages. Le benchmark
ne mesure pas l'OPF/CRC, les bases, ni les relectures imposées par le
commit. Ne pas paralléliser un écrivain, déplacer une revalidation,
affaiblir un verrou ni remplacer l'autorité par un cache SHA pour gagner
du temps. Les scénarios de race existants restent normatifs ; la recette
sur corpus intégré et comparaison des phases (#2565) est le prochain
seuil de décision. Aucune campagne fournisseur réelle.
