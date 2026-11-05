# Audit CI P1 #4209 — home-page (05/11/2026)

## Décision

Qualifier `home-page` comme douzième package produit audité (**12/21** après CI et fusion). Ne pas promouvoir les neuf autres packages non audités. Une modification isolée du code de `home-page` ne requiert que sa suite plugin, **1/21**, car aucun autre package plugin ne dépend de sa contribution `ui.page.home`.

## Contrat de dépendance

- Le manifest `src/plugins/home-page/cifereca-plugin.json` publie `ui.page.home`. Il déclare un `requires_contracts` **optionnel** sur la catégorie `provider.library` / `cifereca.provider`. Le graphe CI référence les producteurs de cette catégorie (l'absence du fournisseur au runtime ne bloque pas l'activation).
- Le code `cifereca_home.py` lit les projections du broker Core public, non les modules internes des fournisseurs. Il classe certains fournisseurs par identifiant (sources publiques, libellés et non-prêteurs), d'où les arcs explicites déjà établis vers `ebooksgratuits`, `provider-bnf-catalogue`, `provider-gallica` et `provider-gutenberg`.
- Par direction **consumer → dependency**, un changement du fournisseur conduit à la suite `home-page`; changer `home-page` ne force pas les tests de ses fournisseurs. Les tests verrouillent ces deux sens ainsi que les nouveaux consommateurs directs d'`ui.page.home` ou `cifereca_home`.
- Les vérifications de widgets GTK, contrats Core et scénarios E2E restent des gates séparés et inchangés ; le présent audit cible uniquement les suites du runner plugins.

## Gardes

- Changement produit isolé `src/plugins/home-page/cifereca_home.py` → `src/plugins/home-page` (1/21).
- Changement de fournisseur connu → fermeture transitive incluant `home-page`.
- Baen non audité, changement mixte incluant Baen, manifest, helper de tests, suppression ou renommage → suites plugins exhaustives (21/21).
- Tout nouvel import/consommateur direct d'`ui.page.home` dans un autre package doit déclencher la garde de non-régression et un réaudit.

Aucune suppression de tests ni modification de produit, de droits ou de CI Core/E2E/installation. Mesurer le gain de durée sur exécutions comparables avant de le revendiquer.
