# Conformité Paris / BnF Prêt — P1 #3122 (25 octobre 2026)

Matrice **conservatrice**, établie sur les vrais manifests et classes
`ProviderSession`, avec tests synthétiques. Les colonnes de disponibilité
ne prouvent ni droit de prêt, ni consentement, ni succès fournisseur réel.

## Échelle de confiance

- **supported** : contrat/méthode concordants et parcours testé hors réseau ;
  ne signifie pas succès live ni consentement pour une mutation.
- **partial** : lecture ou parcours effectif mais couverture, permission,
  pagination ou complétude non démontrées.
- **unsupported** : aucune capacité publique revendiquée ; l'existence
  d'une méthode interne ne suffit pas.
- **unverified** : recette utilisateur réelle encore nécessaire ou preuve
  inexistante ; ne jamais inventer `complete` ou `confirmed`.

## Tableau de conformité actuel

| Fonctionnalité | Paris | BnF Prêt | Limite attestée |
| --- | --- | --- | --- |
| Manifest + `sync.loans` + méthode réelle | **supported** | **supported** | Inspection statique sans I/O |
| Liste positive de prêts | **supported** | **partial** | Clients simulés ; BnF HTML ne prétend jamais être complet |
| Exhaustivité / disparition du prêt | **unverified** | **unsupported** | Paris DILICOM/SYRACUSE ne déclarent pas une fin complète de pagination ; BnF est partiel même à zéro |
| Multi-comptes/lecteurs | **supported** | **unsupported** | Paris : compte primaire et comptes liés ; BnF : une session lecteur |
| Catalogue public recherche/détail | **partial** | **partial** | Paris `all` seulement, branches indépendantes ; BnF pages HTML bornées à 5 |
| Emprunt public (`loan.borrow`) | **partial** | **unsupported** | Paris : manifest+méthode, droits/consentement et recette live à vérifier ; BnF : rien d'annoncé |
| Restitution (`loan.return`) | **partial** | **unsupported** | Paris : méthode legacy, manifest `lending.return` absent ; confirmation distante distincte |
| Renouvellement (`loan.renew`) | **partial** | **unsupported** | Paris : méthode legacy, manifest `lending.renew` absent |
| Récupération de jeton (`loan.download_token`) | **partial** | **unsupported** | Paris : manifest+méthode ; BnF : méthode interne, aucune capacité publique |
| Staging ACSM privé après double accord | **unverified** | **partial** | BnF : chemin protégé, candidats locaux, résultat réel encore à qualifier |
| Acquisition automatique à l'ouverture | **partial** | **unsupported** | Opt-in explicite Paris historique seulement ; BnF `False` |
| Fulfillment ADEPT et preuve de fichier | **unverified** | **unverified** | Dépend du profil ADEPT/emprunteur et de recettes réelles |
| Sync Core explicite / ouverture sans absence destructive | **supported** | **partial** | #3152 fusionnée, ouverture #3156 en validation ; BnF demeure partiel |

**Ne pas confondre** `supported` au niveau déclaration technique avec
une action disponible pour un livre précis. Les statuts complets de
l'issue #3121 exigent ensuite consentement, autorisation du prêt exact,
revalidation session/epoch, et preuve de résultat distant.

## Preuves automatisées

- `tests/test_core_p1_3120_lending_capabilities.py` : validateur statique
  des capacités et disponibilité des sessions simulées.
- `tests/test_core_p1_3122_real_manifest_lending_conformance.py` :
  **vrais** manifests/package versions/classes Paris/BnF, absence de
  promotion du `download_digital` interne BnF, séparation catalogue/prêt.
- `tests/test_core_p1_3122_paris_bnf_snapshots.py` : vrais adaptateurs
  branchés sur HTTP/HTML synthétique : Paris branches, BnF incomplet,
  comptes, changements d'état.
- `tests/test_core_p1_3119_3122_explicit_snapshot_sync.py` : consommation
  dans Core Sync explicite, invalidation et absence non destructrice.
- `tests/test_core_p1_3119_3122_opening_typed_inventory.py` :
  proposition #3156, en attente de validation CI au moment de ce tableau.

## Recettes restant ouvertes

- Paris : disponibilité/permission précise d'emprunt/retour/renouvellement,
  vérification réellement complète des branches et absence sur PNB.
- BnF : connexion et prêts visibles ne suffisent pas à valider les dates,
  pagination complète, jeton ACSM réel, ADEPT et restitution.
- Boutons GTK, cache/offline, rollback de packages réels, et preuve de
  non-régression côté utilisateurs restent à vérifier avant fermeture #3122.
- Les transitions destructrices des droits restent régies par **#2975 P0** ;
  ce lot n'y apporte aucune autorisation nouvelle.
