# Core P0 #2797 / #3171 — Numilog : repli après précontrôle

## Production observée le 30 octobre 2026

Journal `cifereca-journal-brut-20261030-062212.zip`, uniquement à partir de **06:16:34 Paris**.
À 06:17:41–06:17:42 : six achats Numilog examinés, positions 1–3
`already_local_verified`, positions 4–6 `needs_reconciliation`, sortie
`preflight_failed`, `attempted=0`. Cette observation **ne prouve pas**
que ces trois fichiers étaient absents, ni quel reçu/Placement/SHA était
incorrect ; les données SQLite et les fichiers de production ne sont pas
inclus dans l'export.

## Comportement visé

- Le précontrôle passif ne crée pas de droit. Le manifeste privé authentifié
  et l'entitlement exact `(identité, credential, compte, item, édition)`
  restent requis. Une offre non téléchargeable ou un consentement `on_start`
  absent/révoqué ne peut jamais lancer de GET.
- Une preuve complète (reçu, Placement physique et SHA final du bon profil)
  fait **zéro GET** ; un changement d'emplacement n'est pas une modification
  d'identité bibliographique. Un SHA identique d'un autre compte n'attribue
  aucun droit.
- `needs_reconciliation` signifie **présence non prouvée**, pas refus
  de livraison. Le `recovery_gate` vérifie uniquement les conflits de droits,
  de propriétaires, de reçus et de Placement. Une ancienne acquisition
  *complete* sans preuve physique peut faire une nouvelle livraison par le
  chemin Numilog existant avec son consentement source persistant.
- Le téléchargement se fait sous le verrou Core global et conserve les
  revalidations session/droit/compte/consentement avant et après le fournisseur,
  le traitement ADEPT, le SHA final et la publication. Le nouveau reçu
  `numilog-materialization-recovery` ne remplace **jamais** le reçu historique.
  Un nom cible déjà occupé entraîne une destination bornée
  `numilog/recovery-<hash>`; aucun fichier existant n'est remplacé.
- Une reprise ambiguë, un reçu inachevé/index_pending, un compte étranger,
  un placement d'un autre profil ou un index en échec sont signalés par
  des **codes fermés**, uniquement pour l'achat en question. Les acquisitions
  éligibles suivantes continuent.
- La seconde Sync ne doit pas retélécharger : la vérification du reçu
  recovery/Placement/SHA se fait sur les octets, jamais sur le dossier ou le
  titre. Toute interruption laisse un reçu avec son statut exploitable par
  la reprise d'index existante, pas une répétition aveugle de l'ACSM.

## Diagnostic sans exfiltration

Corréler les positions de `item_decisions` dans
`startup_materialization` à la tâche et au `run_ref`. Les codes
`recovery_right_unproven`, `recovery_receipt_pending`,
`recovery_foreign_receipt`, `recovery_foreign_placement`,
`recovery_receipts_ambiguous`, `recovery_already_attempted` et
`recovery_evidence_unavailable` sont des refus **locaux**. Ne jamais
journaliser URL signée, cookies, payload fournisseur, chemin privé ou
méta brute. La PR diagnostique #3705 peut préciser les causes de présence
invalide sans changer l'autorité de ce repli.

## Recette et réception

`pytest -q tests/test_core_p0_2797_numilog_recovery.py
tests/test_core_p0_numilog_auto_materialization.py
tests/test_numilog_materialization_checkpoint.py
tests/test_core_p0_numilog_manual_acquisition_lock.py`

Les tests synthétiques ne certifient pas les six EPUB réels. Pour déclarer
la recette production recevable : re-exécuter après déploiement, joindre
le résultat par position, le nombre de GET, les états de reçus, OriginLinks,
Placements et SHA (localement, sans secrets), puis une seconde Sync avec
zéro téléchargement inutile. Conserver le journal avant/après et les
preuves de rollback. Ne pas fermer #2797 avant cette réception.
