# P1 #1867 — Recherche Kobo : catalogue ≠ droit actif ≠ sauvegarde

## Problème et correction

Le résultat brut Kobo `owned=True` (notamment une notice de catalogue)
ne constitue pas une attestation d'achat personnelle. La Bibliothèque
utilise déjà les droits `owned` attestés et persistés du Core, mais
l'admission du bouton « Sauvegarder » consultait uniquement le drapeau
de la notice et la disponibilité du runtime ADEPT.

Désormais, `LibraryService.can_direct_download` exige conjointement :

1. Une identité courante explicite ;
2. Une provenance Kobo privée et précise (`credential_id` non public,
   `record_id` exact, `owned is True`, aucune offre explicitement
   `downloadable=False`) ;
3. Un **droit Kobo `owned/active` confirmé** dans le ledger Core pour
   cette identité + ce credential + cet item ;
4. Une session capable de résoudre l'acquisition et un runtime Adept
   disponible, compatible et opérationnel.

Une notice publique (`owned=True` sans ledger), un achat archivé,
un item d'un autre profil/compte ou un cache hors ligne n'activent
jamais la sauvegarde. Une attestation de possession **ne garantit
ni un EPUB présent ni une offre de téléchargement exécutable**.

## Présentation

Recherche montre :

- « Catalogue Kobo · aucun achat attesté pour ce profil » si aucun
  droit privé correspondant n'est observé (même si `owned=True`) ;
- « Possédé sur Kobo · Mes achats (droit attesté) » pour un droit actif,
  avec « Sauvegarde Kobo indisponible » quand l'admission échoue ;
- « Archivé sur Kobo · droit non actif » pour un ancien achat.

La fiche Recherche affiche le même état à partir de la projection
Core SQLite, sans requête Vault ou découverte supplémentaire.
Le libellé **Disponibilité** du Core suit la même preuve : il ne dit
plus « Dans ma bibliothèque Kobo » si une notice publique porte
`owned=True` mais qu'aucun droit actif n'est attesté pour ce profil.
Il indique « Catalogue Kobo · aucun achat attesté » ou « Achat Kobo
archivé » ; un droit actif est « Mes achats Kobo », avec la sauvegarde
seulement si tous les gardes opérationnels l'autorisent.
Un cache hors ligne reste une notice non actualisée.

La liste lit le ledger au plus une fois par rendu ; l'admission est
consultée seulement pour les droits actifs. L'ouverture de la fiche
reste lecture seule ; aucun consentement implicite, nouvelle commande
d'acquisition, téléchargement, modification de droits ou d'ADEPT.

## Tests

`tests/test_core_p1_1867_kobo_search_purchases.py` couvre les droits
SQLite positifs, l'archive, les comptes/profils distincts, les drapeaux
contradictoires, les notices publiques, l'absence de sauvegarde
implicite et la cohérence des libellés liste/fiche.

La recette fournisseur réelle et l'exécution effective d'une sauvegarde
restent distinctes (#1337, #1905 et #1867).
