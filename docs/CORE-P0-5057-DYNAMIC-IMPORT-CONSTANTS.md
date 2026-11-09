# P0 #5057 — imports dynamiques via constante statique

Le scanner AST Core capture désormais les imports fournisseurs effectués avec `importlib.import_module(NAME)`, `from importlib import import_module as load; load(NAME)` et `__import__(NAME)` lorsque `NAME` est une constante chaîne **au niveau module**, en majuscules, avec **une seule écriture** dans l'AST et au plus 512 caractères.

Exemple bloqué : `MODULE = 'cifereca.numilog.adapter'; importlib.import_module(MODULE)`.

Les constantes redéfinies, masquées par un argument local, interpolées, calculées ou transmises à une fonction autre qu'un import Python reconnu ne sont pas évaluées ni inférées. L'inventaire reste statique : pas de runtime, accès réseau, import plugin, effet de bord, ni prétention à la reachability complète. Le gate reste monotone **par fichier, catégorie et fournisseur** sur les commits Git base/head exacts.

Reste à couvrir les dispatchs dynamiques non statiquement qualifiables et à prouver l'absence de désactivation du gate ; #5057 demeure P0 ouvert tant que les critères du ticket ne sont pas complètement démontrés.
