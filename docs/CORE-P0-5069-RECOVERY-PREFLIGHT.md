# #5069 — Diagnostic de reprise de l'Updater (tranche A1)

## Commande opérationnelle (lecture seule)

```sh
cifereca-update --recovery-inspect --json
```

Le résultat fournit les transactions incomplètes, leur ID validé, leurs états, des motifs fermés (`before_previous_missing`, `before_current_missing`, `core_link_mismatch`, `plugin_snapshot_missing`, `plugin_state_mismatch`, `transaction_unreadable`, etc.) et l'action générale recommandée. Les chemins de releases, packages et profils ne figurent pas dans le diagnostic. Le diagnostic vérifie les identités de packages et peut donc nécessiter une lecture locale de leurs fichiers ; il n'écrit rien et ne lance aucun plugin ni accès réseau.

## Préflight

Les voies mutatrices `--github`, archive, source et `--rollback` vérifient désormais, avant téléchargement GitHub ou préparation d'installation, les transactions dont la restauration est déjà déclarée `restoration_incomplete`. Une preuve de rollback indispensable manquante ou dangereuse provoque un refus explicite avec le numéro de transaction et un code fermé. **Aucune suppression, réparation ni requalification d'une restauration non prouvée.** Les autres transactions interrompues restent traitées par le mécanisme de reprise existant.

Si `before_previous_missing` est signalé, récupérer la génération Core originale depuis une sauvegarde intègre, puis contrôler de nouveau. **Ne pas créer de répertoire ou de symlink factice** et ne pas modifier manuellement `transaction.json`.

## Portée exacte de cette PR

Ce lot fournit **diagnostic + échec précoce**, pas le déblocage supervisé définitif. La réconciliation explicite et auditable de `before_previous` irrécupérable, les verrous de sérialisation, puis la rétention universelle 24 h + minimum 2 relèvent des lots suivants de #5069. Ne pas annoncer l'incident résolu avant une récupération physique ou une réconciliation correctement attestée.