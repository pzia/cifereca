# P1 #1867 — boutons Numilog : achat privé ≠ notice de catalogue

## Problème

Un fournisseur Numilog connecté possède une capacité générale
`direct_download=True`. Cette capacité **ne prouve pas** qu'une
`SearchResult` donnée est un achat de ce compte. Une recherche
`scope=catalog`, une notice commerciale dans le cache d'un compte
personnel, ou une fiche mal typée ne doivent jamais afficher
« Télécharger » ni « Emprunter » du seul fait que la session est connectée.

## Admission en lecture seule

La suggestion d'une acquisition Numilog exige **simultanément** :

- origine `numilog`, collection privée `library`, scénario
  `NUMILOG_LIBRARY`, `owned is True` et `downloadable is True` ;
- credential privé non public, identifiant `numilog:{scope}:{lid}:{variant}`
  valide pour ce credential et livre numérique ;
- absence de `download_url` fournie par la notice et de provenance
  `known_catalog_stale` ;
- **une seule liaison de compte privé en mémoire** pour ce credential,
  avec identité correspondant au profil actif.

Les notices du provider anonyme `numilog-catalog` sont refusées
avant toute résolution de session. La liste Recherche et la fiche
appliquent aussi la séparation `remote_owned` / `remote_catalog`,
même si un ancien adaptateur renvoie naïvement `True` sur
`can_direct_download`.

Cette admission n'est **ni un reçu d'achat, ni un consentement
d'acquisition**. Le vrai téléchargement reste manuel, sous ses gardes
Core/Numilog existants : compte, acquisition déjà publiée, identité,
révision/édition et vérification distante sous verrou. Aucun scan,
cookie, téléchargement, ACSM ni effet fournisseur n'est ajouté au rendu.
Un achat privé non téléchargeable peut rester indiqué comme « Mes achats »
sans que le bouton soit proposé.

## Recette

`tests/test_core_ui_p1_1867_numilog_direct_download_scope.py`
couvre le privé valide, variantes sans provenance, droits ambiguës,
achat sous une autre identité, faux attributs du catalogue, absence
d'appel au fournisseur et les boutons liste/fiche. La CI et le parcours
GTK existant servent de contrôle global. Qualification réseau réelle
de Numilog et acquisition manuelle restent suivies par les tickets P0
#1886, #1905 et #2024. #1867 reste ouvert pour la recette réelle
multi-profils et la cohérence UX transversale.
