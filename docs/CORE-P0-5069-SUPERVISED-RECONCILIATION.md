# P0 #5069 — Réconciliation encadrée « previous » irrécupérable (tranche A2)

> La procédure ne reconstruit jamais une release perdue. Elle archive la preuve du défaut et autorise une mise à jour ultérieure en reconnaissant une restauration **dégradée**. N'utiliser que pour le cas unique documenté, sur une installation locale dont les données et packages sont stables.

## Cas important : Updater installé encore bloqué sur l'ancienne version

La commande `cifereca-update` déjà installée peut ne pas connaître `--recovery-reconcile` et rester bloquée sur `recover`. **Ne pas essayer de l'utiliser pour mettre à jour l'Updater avant d'avoir levé le blocage.** Depuis un checkout Git vérifié de la branche intégrant cette réconciliation, dans la racine du dépôt :

```bash
python3 --version  # Python 3.11 minimum
PYTHONPATH=src python3 -m cifereca.updater --recovery-inspect --json
PYTHONPATH=src python3 -m cifereca.updater --recovery-reconcile ID_TRANSACTION --json
```

Après revue du plan et validation de la perte irréversible de `previous` uniquement :

```bash
PYTHONPATH=src python3 -m cifereca.updater --recovery-reconcile ID_TRANSACTION \
  --acknowledge EMPREINTE_64_HEX --json
```

Ce mécanisme exécute l'Updater depuis **les sources examinées**, sans installer la release ni modifier les binaires avant le consentement. Il doit être utilisé dans l'environnement du compte utilisateur dont `runtime` est concerné ; ne pas recourir à `sudo` pour contourner les permissions. Avant exploitation, s'assurer que la version source a été fusionnée et qualifiée CI sur le SHA exact. Aucun ZIP de compte, journal privé ou chemin sensible n'est à publier sur GitHub.

## 1. Diagnostic read-only

```bash
cifereca-update --recovery-inspect --json
```

La transaction doit être en `restoration_incomplete`, avec un seul motif `before_previous_missing`. Toute autre transaction incomplète, tout écart sur `before_current`, les symlinks, les packages, snapshots ou preuves bloque la réconciliation.

## 2. Plan read-only et empreinte

```bash
cifereca-update --recovery-reconcile ID_TRANSACTION --json
```

Vérifier `reason`, `proposed_state` et `acknowledgement_sha256`. Le plan ne crée aucun fichier et n'exécute aucun plugin. Reprendre l'empreinte **exacte** dans l'étape suivante uniquement après avoir accepté la perte du pointeur `before_previous` original.

## 3. Application volontaire

```bash
cifereca-update --recovery-reconcile ID_TRANSACTION --acknowledge EMPREINTE_64_HEX --json
```

L'opération acquiert un verrou exclusif persistant, réévalue la preuve sous verrou, refuse les changements intermédiaires, sauvegarde un reçu local privé `runtime/reconciliation-audit/ID-*.json` (permissions 0600, fsync) avec la transaction originale complète puis inscrit `reconciled_degraded` dans le journal **sans toucher aux copies/snapshots**. La génération manquante reste manquante ; `rolled_back` et `update_failed_restored` ne sont jamais émis.

Une transaction distincte non finalisée ou un état ambigu empêche toute application. L'audit est conservé durablement et ne doit pas être purgé dans le cadre de la rétention automatique.

## 4. Reprise

Après `recovery_inspect` sans bloqueur, reprendre `cifereca-update --github main`. La nouvelle mise à jour crée sa propre transaction et sa propre preuve. Le rollback sur la génération qui a été réconciliée reste limité par la preuve de restauration originale indisponible. Conserver une sauvegarde indépendante avant toute nouvelle installation.

## Périmètre

La sérialisation couvre les points d'entrée officiels Python (GitHub, archive, source, rollback) et cette réconciliation. Elle ne remplace pas une politique globale de verrouillage des modifications de packages réalisées par des processus tiers extérieurs à l'Updater. Le lot B de #5069 étend séparément la rétention **24 h + minimum 2** aux magasins relevant réellement de l'Updater ; aucune suppression de profils ou données utilisateur n'est introduite ici.