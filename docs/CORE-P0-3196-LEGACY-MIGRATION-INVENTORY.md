# P0 #3196 — inventaire SQL en lecture seule préalable à la migration 0.7

Le Core ne doit pas traiter `backup_items` comme une preuve de possession,
de présence physique ni comme un veto de téléchargement. Les anciens
`backup_put` restent toutefois accessibles pour les connexions sans ABI
canonique complète ; ils ne peuvent être supprimés avant migration et
reprise vérifiées.

## Primitive ajoutée

`cifereca.core.legacy_backup_migration_inventory.inventory_legacy_kobo_backups(storage, identity_id=..., max_rows=1000)`

- Instantané SQLite de l'identité concernée, **sans I/O fournisseur, sans
  parcours de fichiers et sans écriture**. Scan borné à 1 000 lignes Kobo.
- Si une **seule** ligne Kobo sans `identity_id` existe ailleurs, résultat
  `scope_unverified` et **aucun compteur partiel** : ne jamais attribuer
  des droits à partir d'un titre, ISBN, SHA ou compte sélectionné.
- Si le budget est dépassé, `budget_exceeded`, sans compteurs trompeurs.
- Décompte des lignes `complete`, `acquiring/pending_acquisition`,
  `reconcile_required`, `skipped_archived` et états inconnus ; révisions,
  chemins, empreintes manquants, collision exacte de `local_path`.
- `item_id_unqualified` compte les identifiants historiques sans correspondance
  sûre avec le contrat fournisseur (`item_id` vide, espacé en bordure,
  NUL/CR/LF ou supérieur à 1 024 caractères). Leur conservation pendant le
  prune 0.7 ne les rend **pas** candidats à la migration. Ils restent
  `receipt_unproven` et sont comptés `account_unbound` par précaution,
  même si la connexion du compte est valide. Aucun identifiant brut n'est exporté.
- Contrôle de la connexion **actuellement active** et de son emprunteur
  explicite ; le compte Kobo vide n'est admis que s'il est enregistré
  exactement comme tel. Les autres identités/credentials sont opposables.
- Inventaire du `acquisition_ledger` pour la même source, de ses collisions
  de propriétaire et doublons, et des **formes** de reçus Core terminés
  liés au chemin et au SHA. Une forme de reçu n'est pas sa preuve cryptographique
  ou une preuve d'OriginLink : ce compteur reste exclusivement consultatif.
  Le compteur `receipt_unproven` inclut **toutes** les lignes scannées,
  même lorsqu'une forme de reçu a été reconnue ; aucun reçu n'est attesté
  par ce seul inventaire.
- Aucun titre, chemin, compte, credential, fichier, reçu ou identifiant source
  n'apparaît dans le résultat ; seulement statuts fermés et compteurs.
  `status=complete` signifie *inventaire complet*, et **jamais**
  `migration_ready`, `local_present` ou `right_attested`.

## Consultation opérateur hors réseau (P0 #3196)

Sur l'installation locale qui possède **déjà** la base SQLite v14 :

```bash
cifereca-cli legacy-backup-inventory --identity alice --json
```

La commande ouvre exclusivement la base existante en `sqlite mode=ro`
**avant l'initialisation du Core** : ni session Kobo, ni ADEPT, ni GET, ni
`Storage.__init__` (donc aucune mise à niveau implicite du schéma).
Elle exige une identité enregistrée et ne retourne que les agrégats fermés,
sans chemin, titre, compte, credential, édition ni ligne individuelle.
La limite est de 1 000 lignes Kobo de cette identité ;
`budget_exceeded` ou `scope_unverified` produisent un **échec sans
compteurs partiels**. Une base absente n'est jamais créée, un schéma trop
ancien n'est pas migré : `unavailable` et `schema_unavailable` ont un
code de sortie non nul. Aucun résultat de cette commande n'autorise
`backup_items` à accorder un droit ou ne donne une permission de migration.

L'inventaire permet de mesurer les populations à qualifier sur une
installation réelle, **sans relancer les 522 livraisons Kobo**.

## Étapes strictement restantes

1. Par candidat, joindre les coordonnées **révision/SourceKey/compte** aux
   OriginLinks publiés et au reçu complet **sur le chemin exact**.
   Contrôler toutes les collisions de ledger et de chemins SQLite, pas
   seulement leur forme. Revalider les octets, SHA, inodes et dossiers
   uniques du profil ; l'inventaire SQL seul ne l'autorise pas.
2. Après admission explicite, transaction de migration idempotente sous le
   verrou d'acquisition, postcheck et rollback ; ne pas multiplier les droits
   depuis `backup_items`, conserver ADEPT et les checkpoints interrompus.
3. Déplacer les consommateurs restants vers la projection Core en lecture
   seule et démontrer que les anciens writers ne portent plus de scénario
   de reprise requis. Retirer alors leurs writes, mais préserver les
   archives historiques sans destruction.
4. Valider sur fixtures `acquiring/reconcile_required/complete/skipped_archived`,
   multi-comptes, chemins déplacés, crash/reprise, octets absents et replay,
   et CI/installation exactes avant merge.

L'inventaire ne produit pas de plan exécutable, ne crée ni droit ni placement
et ne touche aucun livre utilisateur. La Sync canonique #3171 et le gel
produit de la déduplication #4496 restent inchangés.
