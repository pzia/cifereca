# P1 #4909 — Catalogue Paris mutualisé, droits par profil isolés

## Décision et frontière

Une recherche bibliographique depuis le profil A peut exploiter le catalogue
Paris connecté via B. **La provenance reste `paris` + credential B**, mais
les droits, acquisitions, prêts, achats, quotas et actions restent personnels.
La règle P0 #4673 « ne jamais retomber sur Paris B » est **supplantée pour
la consultation bibliographique uniquement** ; ses gardes de prêt, le choix du
primaire de A et la protection A→B→A restent nécessaires.

## Tranche Core livrée dans cette PR

- `_search_sessions_for_active_identity` préfère la source Paris de A (son
  primaire configuré en premier), puis autorise un catalogue Paris B lorsque
  A ne dispose d'aucune session propre.
- Une session Paris étrangère ou sans propriétaire SQLite **n'exporte que**
  les notices `EBOOKS` / `CATALOGUE` de provenance exacte. Les champs
  `owned`, `downloadable`, `download_url`, `thumbnail_url`,
  `friendly_url`, les offres, révisions, métadonnées et preuves de
  classification supplémentaires sont neutralisés. `catalogue_access =
  shared_read_only` est un marqueur Core *non habilitant*.
- Toute ligne `PERSONAL_LIBRARY`, provenance réécrite, collection `library`
  ou type étranger est refusée. La projection intervient **avant** publication
  de lots progressifs, cache, miroir SQLite ou affichage.
- Les facettes issues d'un compte Paris étranger ne sont pas publiées tant
  qu'elles ne sont pas qualifiées publiques. La recherche reste disponible ;
  les facettes communes peuvent être calculées depuis les notices publiques.
- Les caches `search-v9` et `search-v10-progressive` invalident les anciennes
  entrées non projetées ; ils restent séparés par profil et credential.
- Changement de propriétaire SQLite pendant la requête : annulation de
  l'ancienne recherche. Les changements de session, primaire, profil et
  génération gardent leurs mécanismes existants.
- `LibraryService.holdings` ne fait **aucun GET via B** depuis A : le détail
  présente les données déjà chargées avec disponibilité `unknown`, sans
  holding ni `CanLoan` du compte B. Une projection `shared_read_only`
  reste inerte même si un ancien résultat est conservé pendant A→B.
- `can_direct_download` refuse le credential Paris étranger et la
  projection en lecture seule ; `manual_paris_borrow` conserve le contrôle
  exact de propriété, connexion et primaire.

## Tests et limites

Les fixtures hors-ligne couvrent recherche directe/progressive, cache froid/
chaud, aller-retour A→B→A, session Paris A prioritaire, catalogue public
concurrent, tentative d'injection d'inventaire/URLs privés, GET de détail
interdit et rotation du propriétaire en cours de recherche. **Aucun appel
réel Paris n'est entrepris ici.**

Ce lot ne constitue ni la convergence générale des providers (#2385), ni
l'emprunt interprofils explicite (#4662), ni la recette GTK Recommandations
(#774/#4761). Il n'autorise pas le partage des achats privés Numilog : la
distinction `numilog` / `numilog-catalog` reste à traiter dans son propre
contrat. Les vignettes étrangères sont volontairement masquées jusqu'à une
qualification d'URL strictement publique. Aucune action utilisateur ne doit
être déclenchée par la seule présence d'une notice partagée.


## Tranche Numilog : catalogue anonyme entre profils

Le provider Numilog privé prend deux routes distinctes : `search(scope='all'
ou 'digital')` appelle `library()` / `MesLivres` (achats personnels), alors
que `search(scope='catalog')` appelle la recherche publique anonyme sans
cookies de compte. Lorsque le credential Numilog sélectionné appartient à un
**autre** profil (ou que son propriétaire n'est pas attesté), Core route
**exclusivement** vers `catalog`, avec `field='all'` et post-filtrage local.

Les seules lignes admises sont `numilog/NUMILOG_PUBLIC/public_catalog` avec
`source_credential='__public__'` et `docbase='NUMILOG'`. Les indicateurs de
possession, téléchargement, URL de livraison, vignette/lien fournisseur non
vérifiés, métadonnées de compte et preuves de classification sont retirés.
Une ligne `NUMILOG_LIBRARY` ou de provenance privée reste interdite même
si le provider renvoie accidentellement un mélange. Les lots progressifs
sont projetés avant publication, avec facettes privées bloquées.

Le détail est purement bibliographique depuis la notice chargée, sans appel à
la session privée B. Le retour à B réactive sa recherche `MesLivres` normale,
sous sa propre identité et son cache ; A ne reçoit jamais ce contenu. Le
cache Search passe en `v10` / `v11-progressive` pour invalider les anciens
résultats non compartimentés. Les rotations de propriétaire SQLite annulent
les requêtes en vol. Aucun fallback authentifié n'est permis si la recherche
anonyme échoue ; la cause reste à traiter comme indisponibilité du catalogue.

Limites : la découverte explicite du provider public `numilog-catalog` reste
distincte, et les requêtes du catalogue public peuvent échouer selon le
fournisseur ; un refus public ne déclenche pas la lecture des achats privés.
Tests offline, aucun appel réseau réel.


## Tranche Kobo : limiter les sessions étrangères au catalogue

`KoboSession.search(all|digital)` peut consulter `/library/search` avec
le compte connecté, alors que `scope=catalog` interdit cette branche
privée et ne requiert que le catalogue public. Quand A n'est pas le
propriétaire attesté du credential Kobo B, Core demande explicitement
`scope=catalog` avant tout I/O ; il ne tente jamais la recherche privée
sous B et ne réinterprète pas une absence de résultats publics comme absence
d'achat de B.

Seules les notices `KOBO/catalog` de provenance du credential exact
sont admissibles, avec `owned` et `downloadable` non positifs, sans
`download_url`. La projection Core supprime URL et métadonnées privées,
revision, vignette et lien de fiche non qualifié, et marque l'accès
`shared_read_only`. Une ligne `library/archived`, un achat positif ou
une URL de livraison dans une réponse prétendue publique est refusé.
Les détails affichés utilisent les seuls attributs déjà chargés, sans
requête de droits du compte B, et un changement de propriétaire révoque
l'ancienne recherche.

Les caches Search passent à `v11` et `v12-progressive`, avec séparation
identité/credential. Testé hors réseau avec un fournisseur synthétique à
routes `catalog` et `library` distinctes, en synchrone et progressif.
Aucune évolution ni installation KVault, aucun essai de téléchargement et
aucune permission ajoutée. L'identité propriétaire B conserve sa propre
recherche privée quand B est le profil sélectionné.
