# #3882 — Audit acquisitions / rapport ciblé : contrat CLI

## Commandes (Core, sans branche fournisseur)

Audit **hors réseau et lecture seule** (SQLite `mode=ro`, sans démarrer Core ni
charger les plugins, sans migration ni modification de fichiers) :

```bash
cifereca-cli acquisition-audit --provider numilog --identity ID --verify-files --json --output /tmp/audit-numilog.json
```

Une source active unique parmi les liaisons **persistées** est automatiquement
retenue. Zéro source donne `source_not_configured`, plusieurs
`account_ambiguous` ; `--credential` et `--account-id` sont des
sélecteurs **facultatifs**. Aucun compte d'une autre identité n'est cherché.
Si une ancienne base ne dispose pas des tables canoniques requises, le
diagnostic échoue sans exécuter la moindre migration.

Rapport et exécution en une commande :

```bash
cifereca-cli sync run --provider numilog --identity ID --confirm --report /tmp/numilog-sync-report.zip
```

`--confirm` reste obligatoire et ne contourne pas le consentement OFF.
Préflight du chemin avant toute connexion fournisseur : refus si destination
préexistante/symlink ou suffixe incorrect ; publication atomique sans
écrasement, mode 0600 et taille plafonnée à 8 Mio. Une erreur métier ou un
`partial` après admission produit le ZIP, avec `exit_code` non nul ; les
erreurs de sélection avant admission ne créent pas de ZIP. Deuxième
`--trace-export RAW.zip --confirm-trace-export` toujours indépendant,
jamais inclus automatiquement dans le rapport.

## Exactitude des preuves

Le rapport comprend `manifest.json` (identité du build #3885 et
statut/horaires/run_ref), `sync-result.json`, audits `before/after`,
`events.jsonl`, `summary.txt`. Seuls des événements de phases CLI
explicitement liés à cette exécution sont émis actuellement
(`admission`/`sync_result`). **Les événements par livre et task_ref ne sont
pas encore corrélés automatiquement** : aucune promesse de trace réseau
complète n'est faite, et `events_coverage` doit être interprété comme une
couverture limitée aux phases CLI. Le journal brut reste distinct.

Deux familles de preuves sont distinguées :

- **SourceReference + OriginLink** : origine fournisseur exacte ; ce lien ne
  provient jamais d'une recherche par titre, d'un ISBN ou du dossier physique.
- **AcquisitionLedger + Artifact + Placement** : le reçu exact du compte peut
  attester des octets en staging même si aucun OriginLink n'existe encore.
  `staged_visible_in_library` ne vaut pas publication. `complete_local`
  exige un Placement final du bon profil, un SHA vérifié et l'origine exacte.

Les comptes et identifiants de livres sont rendus opaques avec un sel local
à la commande ; aucun chemin absolu, titre, URL signée, cookie, contenu HTTP
ou message d'erreur libre n'est exporté dans le ZIP. L'audit ne parcourt que
les placements/reçus indexés ; un fichier de staging **non indexé du tout**
n'est pas découvert par balayage, et l'absence de SourceReference n'est pas
la preuve qu'un achat fournisseur n'existe pas.

La racine de staging Core est l'espace technique
`<dossier SQLite>/acquisition-work` ; une autre racine spécifique doit être
fournie explicitement pour être vérifiée. Le rapport ne compare pas des
fichiers dont la localisation sort de ces périmètres attestés, ni des droits
entre comptes. Il ne répare, publie, rapproche ou déduplique rien.

## Ce qui reste dans le P0 #3882

1. Corréler les événements par téléchargement (GET, ADEPT, staging,
   publication, OriginLink), en conservant les run/task refs, sans capturer
   les tâches d'autres profils ni recopier le journal global.
2. Couvrir les fichiers en staging **non indexés** par un inventaire borné
   explicitement consenti, ou des receipts techniques vérifiables.
3. Faire la recette Numilog authentifiée à six acquisitions sur un vrai
   déploiement, puis deuxième Sync idempotente, sans toucher aux cinq fichiers
   staging et au fichier sauvegardé hors FS mentionnés le 31/10.
4. Ne pas présenter l'inventaire `source_sync_partial` comme un échec
   individuel de livraison. Une preuve HTTP 200 ne prouve pas la publication.

Dépendances #3171, #2797, #3848, #3885, #3900.
