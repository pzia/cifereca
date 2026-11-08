# #5069 B4 — Diagnostic de maintenance Updater, sans suppression

Exécuter depuis une version contenant B1–B4 :

```sh
python3 -m cifereca.updater_maintenance_inspect --json
```

La commande agrège les inventaires en lecture seule des générations Core, transactions, snapshots et versions des plugins installés. Elle donne des **comptages par magasin**, les décisions théoriques `retain/candidate` et les limites de couverture ; aucune donnée de profil, chemin, ID package ni fichier de livre n'est exposé.

**Les candidats sont théoriques, NON des instructions de suppression.** `safe_to_apply=false` dans tous les cas. Le mode `--apply` est volontairement implémenté comme un refus fermé, sans toucher au système de fichiers. L'absence d'incident n'est pas une preuve de graphe cross-store complet.

Le code manque encore la qualification d'ownership des backups updater, les artefacts de staging et le verrou partagé avec les mutations des plugins. Le futur `cifereca-update maintenance inspect/plan/apply` exigera préflight exact, plan signé/attesté, revalidation TOCTOU, journal d'audit et reprise crash-safe. Aucun GC universel n'est installé dans B4.