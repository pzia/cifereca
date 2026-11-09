# P0 #5057 — Gate monotone des tables de dispatch fournisseur

Le scanner `scripts/ci_provider_core_coupling_inventory.py` détecte
maintenant trois catégories indépendantes de couplages dans les sources
installées du Core : `imports`, `comparisons` et `dispatch_tables`.

Une table `{"kobo": handle_kobo, "numilog": lambda: ...}` est un
dispatch fournisseur **exécutable**, même sans test `if provider_id ==`.
L'AST repère les clés fournisseurs explicites et constantes de module
immuables ainsi que les valeurs fonction/lambda et les tables déclarées
comme handlers, routes, dispatchers, actions ou callbacks. Le scanner
n'exécute aucun import fournisseur et ne lit pas les secrets.

Les dictionnaires descriptifs `{"kobo": "Kobo"}` et les métadonnées
passives ne constituent pas un signal de dispatch ; elles ne doivent
pas provoquer de faux positif. Le contrôle Git
`scripts/ci_provider_coupling_delta_gate.py` compare séparément
chaque catégorie et identifiant sur les objets commit exacts base/head
et bloque une augmentation, même si un autre type de dette baisse.

Le gate reste **monotone et non une preuve de reachability** :
des dispatchs dynamiques indirects, aliases opaques ou types
externes peuvent demeurer invisibles. La baseline ne certifie pas
la neutralité totale du Core ; le seuil zéro est renforcé sur les
modules déjà migrés dans
`tests/test_core_p0_5057_neutralized_module_gate.py`.
