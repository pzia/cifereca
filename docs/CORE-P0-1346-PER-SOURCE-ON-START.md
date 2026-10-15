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

## Configuration dans l'interface Synchronisation

La page **Synchronisation** montre pour chaque source Kobo une ligne distincte
**« Acquisition Kobo · au démarrage »** :

- Par défaut : **Mode manuel**. « Mettre à jour les notices Kobo en ligne »
  et « Synchroniser les sources » ne téléchargent aucun EPUB.
- **Activer au démarrage…** ouvre un dialogue de confirmation décrivant la portée
  exacte (un seul compte), les risques et les contrôles, puis appelle
  `set_local_materialization_schedule(source_id, "on_start")` ; il n'y a
  aucune acquisition au clic et aucun téléchargement pendant l'ouverture du
  dialogue.
- **Désactiver au démarrage** révoque immédiatement le consentement, même si
  le compte est désormais déconnecté ou sa source désactivée.
- Si le broker ou la capacité n'est pas disponible, le contrôle est
  désactivé. Une identité changée, un dialogue devenu obsolète ou une fenêtre
  fermée n'autorise aucune mutation.

Il n'y a **pas de mode périodique** : cette version ne crée aucun timer
récurrent, et ne doit jamais afficher de périodicité factice.

## À réaliser avant clôture #1346

- Planification périodique réelle de l'inventaire si retenue, distincte
  de l'acquisition (actuellement uniquement au démarrage ou manuel).
- Recette GTK 1366/1600 et trois identités sur installation réelle, avec
  autorisations explicites et compteurs d'acquisition vérifiés.
- CI/CD : tester HEAD exact et intégrer les PR Core/GUI sans double départ.

Le nouveau réglage ne garantit ni la réussite d'ADEPT/DRM ni l'absence de
restrictions fournisseur : les blocages et acquis partiels doivent rester
visibles comme tels.
