# Audit CI P1 #4209 — maintenance-library-audit (05/11/2026)

## Périmètre

Ajouter **un** package audité, `maintenance-library-audit`, aux
deux packages déjà qualifiés (`dev-updater`, `literary-criticism`).
Les 18 autres packages restent soumis aux 21 suites plugin sur tout
changement produit. Le sélecteur, les gates et les workflows sont inchangés.

## Inventaire et fermeture

- `cifereca_library_audits.py` : logique des audits/traitements de
  bibliothèque, modules Python standard et API publique Core
  (`cifereca.plugin_api`). Aucun import de code d'un autre package plugin.
- `cifereca_dedup_recovery.py` : récupération supervisée des
  nettoyages canoniques, dépend de `cifereca.plugin_api` et du broker
  Core `library.maintenance`, sans import d'autres packages.
- Manifest `org.cifereca.maintenance.library-audit` : sept outils
  de catégorie `maintenance.tool`, **sans `requires`,
  `requires_contracts` ni `recommends`**. Brokers Core déclarés :
  `catalog`, `metadata`, `library.read`, `library.naming`
  et `library.maintenance`.
- Aucun des vingt autres manifests plugins ne requiert la catégorie
  `maintenance.tool` ni ces IDs d'outils ; aucun consommateur spécifique
  dans les modules, tests ou manifests d'un autre package plugin.
  Le Core, l'UI de maintenance et des tests Core utilisent ce contrat,
  mais ne sont pas des **suites de packages plugins** ;
  leurs gates restent distincts et inchangés.

## Contrat de non-régression

`tests/test_ci_plugin_selection.py` garantit :
- sur modification produit isolée de chacun des deux modules du package :
  fermeture transitive **1/21** ;
- sur modification Baen non auditée (seule ou mixte), manifest,
  helper partagé, suppression ou renommage : repli exhaustif **21/21** ;
- si un consommateur spécifique apparaît dans les fichiers Python,
  manifestes ou scripts d'un autre plugin : échec du contrat d'audit.

La présence de la catégorie générique `maintenance.tool` dans le Core
ne signifie pas qu'un autre package plugin consomme ce package.
Toute nouvelle interaction inter-package ou dépendance déclarée
nécessite une révision avant d'élargir la sélection.

## Validation et mesure

PR CI obligatoire sur HEAD : le changement de graphe déclenche
l'intégralité des suites Core et plugins. Une mesure de temps **ciblée**
sera possible à l'occasion d'un changement de code réel du package,
sans déduire un gain runtime hypothétique.
