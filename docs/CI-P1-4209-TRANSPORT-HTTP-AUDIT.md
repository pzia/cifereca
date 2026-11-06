# P1 #4209 — audit de périmètre CI : transport-http

## Preuves conservatrices

Le package `transport-http` (manifest `src/plugins/transport-http/cifereca-plugin.json`) livre deux contributions dans **un même paquet** : `transport.http` (HTTP/HTTPS borné, garde d'origine, publication atomique et preuve de protection Core) et `diagnostics.http-transport` (diagnostic local, `requires` intra-package sur `transport.http`). Leur couple n'autorise **aucun** découpage en suites distinctes.

Les manifests des quatre dépendants directs imposent `requires: transport.http` :
`baen`, `ebooksgratuits`, `provider-gallica`, `provider-gutenberg`.
La contribution `home-page` consomme les catalogues publics via le contrat facultatif `provider.library` et ses dépendances explicites ; elle appartient à la fermeture. Même si une seule fonction du transport change, les **six suites** suivantes sont requises :

```
baen
ebooksgratuits
home-page
provider-gallica
provider-gutenberg
transport-http
```

Cette sélection n'élimine **aucun** test de ces suites. Les gates Core, E2E, packaging, installation et sécurité sont indépendants, inchangés. Les tests du sélecteur contrôlent les deux modules transport et la fermeture ainsi que le repli exhaustif sur :
- package non audité en modification mixte, tel que Baen ;
- `cifereca-plugin.json`, helpers, suppression, rename ;
- changement Core partagé, notamment `src/cifereca/services.py`.

Les imports et consommateurs du transport sont gérés par son manifeste et par les tests transverses ; l'absence d'un arc manifesté nouveau doit déclencher une revue du graphe avant toute extension du périmètre audité. **Ne pas revendiquer de gain de durée avant mesure sur runs comparables**.

## Résultat attendu

`audited_product_packages` passe de **14/21 à 15/21** après fusion. Les **6/21 packages non qualifiés** restent en repli exhaustif. Changement du graph/selector uniquement, aucun code de transport ou d'acquisition modifié.
