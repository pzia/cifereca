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
