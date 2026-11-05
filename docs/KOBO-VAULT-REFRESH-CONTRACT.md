# KoboVault — contrat de rafraîchissement et de mesure (#981)

## Principe

Cifereca distingue trois autorités : son **cache de recherche**, les **droits
observés** dans une source personnelle et l'**admission à acquérir** des octets.
Aucun booléen `force` ne doit changer silencieusement l'autorité d'une autre
phase. Le client `KoboVaultClient` n'offre actuellement **aucun endpoint
d'invalidation / purge du cache du serveur KoboVault**. Réémettre un GET ne
garantit donc pas que KoboVault a interrogé Kobo plutôt que son propre cache.

### Contrat serveur vérifié (pzia/KVault v0.9.7)

Référence : `pzia/KVault/src/worker.js` :

- `GET /api/v2/catalog/search` consulte **systématiquement** la Cloudflare
  Cache API, TTL configuré par `CATALOG_CACHE_TTL_SECONDS` (24 h par défaut).
  Ce chemin Provider **n'accepte pas** de paramètre de forçage explicite ;
  `search(force=True)` de Cifereca ne le contourne donc pas.
- Le bouton navigateur « Actualiser Kobo » passe par un **autre chemin**,
  `POST /api/kobo/catalog` avec le JSON `forceRefresh: true`, capable de
  bypasser **son propre** cache Cloudflare. Il n'actualise **pas** le cache
  Provider : l'API navigateur utilise la clé `/__internal-cache/kobo-catalog-v3`
  (q, langue, taille/index de page), tandis que l'API Provider utilise
  `https://kobo-vault.invalid/__v2_catalog_v3` (q, champ, langue, limite,
  page). Ces clés sont distinctes, même pour une recherche identique.
  Ne pas réutiliser cet endpoint navigateur en Cifereca.
- `PUT /api/v2/vault/{uid}/acquisition-access` réinitialise le **backoff**
  de sync personnelle, **pas** sa date `last_success_at` : republier les
  credentials ne force donc pas la synchro Kobo pendant la fenêtre de 24 h.
- `GET /api/v2/library/manifest` appelle
  `ensureFreshLibraryProjection` **sur la première page seulement**.
  La projection D1 est considérée fraîche pendant 24 h
  (`LIBRARY_SYNC_FRESH_MS`), avec un verrou de rafraîchissement de
  5 min et un backoff de 15 min en cas d'échec. Une nouvelle lecture Cifereca
  pendant ce TTL ne provoque pas de re-synchronisation Kobo. Si la collecte
  échoue, la dernière projection est conservée (stale), sans que le GET soit
  une preuve de données nouvelles.
- `GET /api/v2/library/search` et `GET /api/v2/library/{id}` réutilisent
  la même admission de fraîcheur serveur. Aucun `forceRefresh` Provider
  commun aux deux domaines n'est attesté.

En conséquence, distinguer toujours **« GET effectivement envoyé »**
et **« données effectivement revalidées chez Kobo »**. Un vrai forçage
serveur nécessite un contrat explicite avec scope, singleflight, anti-abus
et signal de fraîcheur/repli ; ne pas le simuler par plusieurs GET.

## Authentification : le token Vault n'est pas le token Kobo (#3252)

**Contrat serveur vérifié :** `pzia/KVault/src/worker.js` sur `main`
(commit `dd2bcbe84908750c1983a2b4c81891164ddde861`).

- `GET /api/v2/status` **sans** bearer annonce les **capacités globales**,
  dont `acquisition_delegation`. Cette liste ne constitue pas une autorisation.
- Le **même GET avec** le bearer du credential Cifereca authentifie ce token
  et filtre `capabilities` selon ses scopes effectifs. Le droit
  `kobo.acquisition.delegate` se matérialise dans la liste par
  `acquisition_delegation`. La route de consultation des offres est nommée
  `acquisition_info` (et non `acquisition`). Les entrées
  `capability_details`, non filtrées, ne prouvent **aucun** scope.
  L'API v2.3.3 **ne renvoie pas** `token_scopes` / `scopes` sur `/status`.
- `POST /api/v2/library/{product_id}/acquisition` vérifie d'abord
  `kobo.acquisition.read`, puis exige `kobo.acquisition.delegate` seulement
  pour `{"mode":"delegated"}`.
- **Les tokens API Vault créés sans scopes explicites n'ont PAS
  `kobo.acquisition.delegate`** : leurs valeurs par défaut sont
  `kobo.catalog.read`, `kobo.library.read`, `kobo.acquisition.read`.
  Le parcours de gestion du Vault (`POST /api/v2/vault/{uid_hash}/api-tokens`)
  permet une émission explicite avec la liste des quatre scopes. La valeur
  émise n'est remise qu'à la création ; la configuration Cifereca doit être
  mise à jour localement et une reconnexion doit revalider le nouveau bearer.
  Ne jamais transmettre cette valeur dans un ticket.
- La présence d'une capacité `acquisition_delegation` dans le statut
  **authentifié** ne suffit toujours pas à une livraison : les credentials
  Kobo conservés dans Vault doivent être disponibles
  (`acquisition_access=configured`), le runtime être opérationnel,
  l'achat et l'offre exacte admissibles, et le GET/DRM validés.
- Un **403 Vault pour scope insuffisant** ne peut pas être corrigé par
  le `refresh_token` Kobo. Ne pas boucler ni rafraîchir Kobo automatiquement
  sur ce 403 ; émettre un nouveau **token Vault** autorisé. Côté Kobo
  sous-jacent, la gestion d'un **401 d'access token Kobo expiré** est une
  opération indépendante du backend, et ne doit jamais affaiblir les
  autorisations Vault.

**Vérification de capacité sans acquisition** : après reconnexion, la vue
diagnostique du provider Kobo doit exposer
`delegation_available=true`. L'état brut de `/status` est contrôlable
localement avec un GET authentifié ; ne communiquer que l'indicateur
`"acquisition_delegation" in capabilities`, ni bearer ni réponse complète
dans GitHub. Un refus reste non-réessayable tant que la réautorisation du
token Vault n'a pas été attestée.

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
