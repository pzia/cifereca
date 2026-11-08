> [!IMPORTANT]
> **Archive historique — P0 #4859, retrait du 08/11/2026.** La commande `kobo-observe-local` et l'option `file_only=True` sont supprimées du Core/CLI. Un constat d'octets 0.7 ne constitue ni un droit fournisseur ni une livraison attestée. Les instructions ci-dessous sont conservées uniquement comme historique et **ne sont plus exécutables**. Utiliser l'audit consultatif `kobo-audit` ou un relink sur preuves canoniques A→B.

# P0 #4859 — Constat offline Kobo sans attribution de droit

La commande dédiée `kobo-observe-local` utilise le Core intégré
`observe_kobo_local_file_only`, sans instancier la bibliothèque ni démarrer
aucun plugin ou provider.

Exemple, en lecture seule (paramètres fictifs) :

```bash
cifereca kobo-observe-local \
  --identity lecteur-a --credential kobo-local-a \
  --account-id borrower-a --item edition-a \
  --sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --size 1024 --path /bibliotheque/lecteur-a/kobo/edition-a.epub
```

L'aperçu produit un `plan_token` opaque ; la même commande complétée
par `--confirm <token>` archive uniquement un constat file-only de ces
octets et de cette portée. La paire SHA/taille et la configuration sont
revalidées ; compte désactivé/inconnu, fichier déplacé, inode renouvelé,
symbole ou vue SQL changée sont refusés.

Cette CLI est **distincte** de `kobo-reconcile-local` : elle n'offre pas
`--undo`, ne relie jamais une édition à un Placement, n'inscrit pas de droit
et n'écrit pas de `backup_items`, d'acquisition ou d'OriginLink. Les
réponses stdout/stderr n'exposent pas les identifiants privés, chemin ou SHA.

L'ancienne commande reste pour rétrocompatibilité W4/D5 pendant le
cutover complet ; ne pas fermer #4859/#3196 sur la seule publication
du constat. Recette :
`tests/test_core_p0_4859_offline_file_only_cli.py`.
