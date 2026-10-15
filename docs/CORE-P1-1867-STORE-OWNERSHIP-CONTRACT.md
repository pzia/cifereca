# Core P1 #1867 — droits d'achat / possession de store ≠ emprunts

## Projection persistée, sans réseau

`LibraryService.identity_statistics(identity_id)` ajoute deux compteurs entiers :

- `owned_store_books` : livres **possédés chez une librairie numérique** dans
  l'identité demandée, avec preuve issue d'un manifeste personnel Kobo
  (`entitlements` actifs) ou de l'inventaire authentifié Numilog
  (`catalog_entries` en `remote_owned` provenant de `source_collection=library`).
  Plusieurs observations du même `provider + credential + item_id` comptent
  pour un seul droit ; deux comptes distincts ne fusionnent jamais par titre.
- `owned_store_sources` : nombre de connexions Kobo ou Numilog distinctes
  dont le rattachement persistant correspond à **un compte unique** de cette
  identité. L'absence de livres et l'absence de comptes sont donc distinguées.

Les métriques existantes `known_loans`, `remote_owned_rights`,
`local_books` et `loan_sources_observed` ne changent pas de sens.
En particulier :

- **possédé en ligne** ne signifie pas **téléchargé localement** ;
- **possédé/acheté** ne signifie pas **emprunté** ni qu'une date limite existe ;
- `known_loans=None` signifie « prêts non observés » et non « zéro emprunt » ;
- une annonce `owned=True` depuis une recherche publique, un fichier
  `local` sans provenance d'achat, un titre seul ou un compte non lié ne
  prouvent pas l'achat.

Seuls des compteurs sont exposés. Aucun identifiant de titre, compte,
credential, lien de livraison ACSM, répertoire ni payload fournisseur n'entre
dans le résultat. Le chemin est en lecture seule : aucun accès fournisseur,
aucun scan de fichiers, aucun téléchargement ni modification d'ADEPT.

## Suite UX / CoreUI

Les widgets Accueil, Statistiques, Recherche et Synchronisation peuvent
afficher `owned_store_books` comme « Livres possédés chez Numilog/Kobo »
et `known_loans` **uniquement** dans un contexte de prêt (Paris/PNB).
La page « Emprunts » doit continuer de ne présenter que de véritables prêts.

Ce premier contrat Core ne change pas les libellés des widgets. La recette
GTK 1366/1600, les cas profil store uniquement / Paris uniquement / mixte
et la communication des états aux plugins sont les étapes UX de #1867.
