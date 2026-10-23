# P0 #1346 — acquisition locale Kobo au démarrage, uniquement par source

## Contrat mis en œuvre

- **Inventaire/droits** : inchangés, indépendants de l'acquisition locale. La
  synchronisation d'ouverture et `synchronize_source` n'autorisent aucun EPUB.
- **Matérialisation locale** : `manual` par défaut (y compris si l'ancienne
  préférence globale `kobo_backup.enabled` est enregistrée à `true`).
  Seule la préférence explicite `on_start` d'une source Kobo précise permet
  l'acquisition lors d'une synchronisation d'ouverture.
- **Pas de `periodic` fictif** : refusé tant qu'il n'existe pas de scheduler
  périodique réellement propre à la source. Le shim GUI ne lance plus de second
  lot Kobo (voir #1851).
- **Migration conservatrice** : ne pas convertir l'ancien booléen global en
  `on_start` silencieusement. Les utilisateurs concernés doivent confirmer
  individuellement leurs sources avant réactivation. Aucun fichier existant
  n'est déplacé ou supprimé par cette migration de politique.

## API Core / broker Sync

```python
broker.local_materialization_schedule(source_id)  # "manual", "on_start" ou "not_supported"
broker.set_local_materialization_schedule(source_id, "on_start")  # opt-in explicite
broker.set_local_materialization_schedule(source_id, "manual")    # désactivation
```

Le `source_id` opaque est obtenu depuis `broker.sources(identity_id)`. Le
broker exige une connexion Kobo active, une capacité de sauvegarde, une source
activée, un **unique** compte pour le credential et un rattachement persistant
identité↔credential concordant. L'autorisation n'est ni transmise dans les
résultats publics ni inférée de l'historique des téléchargements.

## Activation de la source (complément P0 #1346)

La préférence `sync.source.enabled.<source_id>` est un **commutateur distinct**
de l'autorisation `on_start` de matérialisation. Elle gouverne aussi les
actualisations d'inventaire. Son interprétation est maintenant commune à
`LibraryService.sync_source_enabled` et au Sync broker :

- **absente** : activée pour la seule synchronisation des données (compatibilité) ;
- **texte** `1`, `true`, `yes`, `on` (casse et espaces normalisés) : activée ;
- toute autre valeur, notamment `false`, `0`, valeur corrompue ou privée :
  **désactivée** — aucun rafraîchissement implicite au démarrage.

Le Core réévalue la préférence dans `_synchronize_account_source_impl`, sous
le verrou d'acquisition partagé, même lorsqu'un appelant évite le broker.
Une opération ainsi ignorée donne `status=skipped` /
`reason=source_disabled` sans fausse réussite métier, sans appel fournisseur
et sans changement de fichiers. La préférence `on_start` locale reste en
plus strictement contrôlée, sans migration automatique depuis les réglages
legacy ou une préférence de source corrompue.

## Sécurité et cadencement

Avant **chaque** lot ciblé, le coordinateur réévalue l'autorisation (y compris
si l'identité change entre la préparation et l'exécution) et reste sous le
verrou d'acquisition interprocessus de `opening_sync`. Il ne peut plus invoquer
`_sync_kobo_backup_locked` en repli global, même sur `deep=True`.

Les comptes disposant d'un checkpoint conservent leur reprise/backoff
existants. Sans checkpoint (manifeste vide notamment), l'horloge d'**essai**
propre à la source évite les répétitions rapprochées, sans fabriquer un
`last_success`. Si l'horloge ne peut pas être lue ou persistée, l'acquisition
automatique est refusée. Les annulations explicites restent bloquantes.

L'ancien indicateur de configuration et son intervalle restent lisibles pour
la compatibilité/diagnostic. Le bilan distingue `manual_only` de
`disabled`, `fresh_cache`, `partial`, `error` et expose seulement un
nombre borné de sources `scheduled_kobo`, sans identifiants, livres, chemins,
URL, tokens ou messages bruts de fournisseurs.

## À réaliser avant clôture #1346

- UI de programmation par source (et éventuelle planification périodique
  réelle de l'inventaire, distincte de l'acquisition).
- Recette GTK 1366/1600 et trois identités sur installation réelle, avec
  autorisations explicites et compteurs d'acquisition vérifiés.
- CI/CD : tester HEAD exact et intégrer les PR Core/GUI sans double départ.

Le nouveau réglage ne garantit ni la réussite d'ADEPT/DRM ni l'absence de
restrictions fournisseur : les blocages et acquis partiels doivent rester
visibles comme tels.

## Prêts au rafraîchissement explicite — P0 #2975

La synchronisation d'une source autorisée (`synchronize_source`) enregistre
maintenant les **lignes positives** de prêts numériques qu'elle vient de lire,
dans le périmètre exact `identité + fournisseur + credential + emprunteur`,
comme la synchronisation d'ouverture. Elle refuse les origines mixtes,
les comptes supprimés/réaffectés pendant la lecture et les sessions révoquées.
**Aucune liste vide ou partielle n'est une preuve de restitution**, et cette
commande ne lance ni acquisition/DRM, ni retour de prêt, ni expiration globale
des droits d'un autre compte. L'inventaire Paris peut être `partial` si
DILICOM ou SYRACUSE échoue ; seules les lignes positives valides sont
conservées. L'événement public dédié emploie le code fermé
`source_sync_partial`, sans erreur source, identifiant d'emprunteur ou URL.
