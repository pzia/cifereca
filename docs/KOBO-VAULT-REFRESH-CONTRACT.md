# KoboVault — contrat de rafraîchissement et de mesure (#981)

## Principe

Cifereca distingue trois autorités : son **cache de recherche**, les **droits
observés** dans une source personnelle et l'**admission à acquérir** des octets.
Aucun booléen `force` ne doit changer silencieusement l'autorité d'une autre
phase. Le client `KoboVaultClient` n'offre actuellement **aucun endpoint
d'invalidation / purge du cache du serveur KoboVault**. Réémettre un GET ne
garantit donc pas que KoboVault a interrogé Kobo plutôt que son propre cache.

## Opérations et frais réseau

| Déclencheur | Ce qu'il force | Ce qu'il ne force **pas** |
| --- | --- | --- |
| Recherche normale | Une recherche si cache Core expiré/manquant | Refresh des achats/droits |
| `search(force=True)`, `search_progressive(force=True)` ou « Étendre en ligne » | Ignore le résultat de recherche caché dans Cifereca, envoie les recherches fournisseur nécessaires | Purge du cache serveur, relecture complète du manifeste, consentement d'acquisition |
| Synchronisation explicite d'une source `synchronize_account_source` | Nouvel inventaire authentifié + observation canonique ; Kobo peut réutiliser le même manifeste validé **dans cette seule opération** (#3999/#4000) | Cache persistant de droits, purge KoboVault |
| Synchronisation au démarrage (opening) | Observation par source puis miroir de métadonnées ; contexte Kobo mutualisé pour l'ouverture quand #4005 est intégré | Admission automatique de téléchargement, conservation d'inventaire entre ouvertures |
| Politique d'inventaire source `manual/on_start/periodic` | Calendrier d'admission des synchronisations de droits/inventaire (#1346) | Téléchargement local, invalidation du cache serveur |
| `kobo_backup_preview(refresh_manifest=False)` | Aperçu à partir du checkpoint local | Nouveau GET ni droits frais |
| `kobo_backup_preview(refresh_manifest=True)` / aperçu « refresh manifest » | Lecture du manifeste privé distant, sans acquisition | Fichiers téléchargés, changement du cache serveur |
| Materialization/reprise manuelle explicitement autorisée | Vérification du manifeste, consentement source et octets selon les gardes du parcours | Réutilisation automatique de la preuve d'une synchronisation précédente |
| `kobo_adept_state(force=True)` | Rafraîchit l'état de l'intégration ADEPT | GET `/library/manifest` (c'est un autre composant) |

La matérialisation est une **phase distincte** : ne jamais remplacer sa
revalidation par une photographie historique uniquement pour réduire les GET.
Un changement d'identité, de session, de connexion, de broker ou d'époque Core
invalide les snapshots au point de consommation ; un manifeste paginé incomplet
n'autorise jamais un effacement négatif de droits.

## Observabilité fournisseur

Le diagnostic Kobo expose `manifest_request_stats` (PR #4007) :

- `scans_started` : appels à `library_manifest` depuis cette instance client ;
- `scans_paginated` : traversées ayant atteint la fin de pagination valide,
  **pas** une garantie d'autorité des droits si des lignes ont été rejetées ;
- `http_get_attempts` : tentatives de `GET /library/manifest` via ce client,
  y compris réponses d'erreur, mais ni recherches publiques ni acquis ;
- `last_scan_page_attempts` et `last_scan_pagination_complete` : diagnostic
  de la dernière traversée, non un verdict de synchronisation Core.

Tous les compteurs sont bornés, n'incluent ni URL privée, ni curseur, ni
identifiant de livre ou de compte. Ils sont volatils, par processus/session et
best-effort en cas de concurrence ; ils n'autorisent aucune acquisition.
Comparer **les deltas entre deux relevés de la même instance** pour caractériser
une action :

- pour N pages et une Sync explicite Kobo avec #3999/#4000 :
  `+1 scans_started`, `+N http_get_attempts` attendu ;
- pour N pages, observation puis miroir d'une même ouverture avec #4005 :
  `+1 scans_started`, `+N http_get_attempts` attendu, hors autres opérations ;
- après une fermeture/reconnexion ou un nouveau processus, repartir de zéro.
  Aucun compteur Cifereca ne prouve la fraîcheur **interne** au serveur Vault.

## Avant toute nouvelle commande « forcer »

Nommer explicitement l'objet : `search_online`, `sync_source_inventory`,
`refresh_backup_preview`, `materialize_source` ou `refresh_adept_state`.
Ne pas utiliser un `force_refresh` global, qui fusionnerait des consentements,
des TTL et des preuves de droit incompatibles. Toute future API d'invalidation
de cache côté KoboVault devra être spécifiée et testée **dans ce serveur**,
avec scope compte/credential, authenticité de la réponse et politique
d'invalidation ; elle ne doit pas être inventée par Cifereca.
