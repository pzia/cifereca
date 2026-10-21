# P1 #2555 — Emprunts : éviter un faux « aucun prêt »

Le premier affichage de **Mes emprunts** ouvre maintenant le segment
**Tous** (physiques **et** numériques). Auparavant le segment Numériques
était présélectionné : des prêts physiques déjà récupérés pouvaient
rester masqués et donner l'impression d'un inventaire vide.

Les trois filtres restent disponibles, les compteurs affichent le
volume par medium et la sélection Langues continue de limiter le rendu
uniquement sur demande. L'état vide est désormais formulé
« Aucun emprunt affiché » : il invite à vérifier l'identité, les filtres
et l'actualisation, sans conclure à l'absence de prêts chez Paris ou
un autre fournisseur. L'action d'actualisation reste la **lecture de
l'inventaire de l'identité active**, sans emprunt ni retour.

**Ce lot ne valide pas** l'état de la connexion Paris et ne transforme
pas une erreur DILICOM/SYRACUSE en résultat zéro : une panne partielle
de l'inventaire nécessite toujours un suivi des diagnostics Core/Paris
et la recette utilisateur décrite au ticket. Le P1 #2555 reste ouvert
tant que ces preuves terrain ne sont pas disponibles.

Recette : fixture sans prêt numérique mais avec deux prêts physiques
→ premier affichage « Tous · 2 », « Physiques · 2 »,
« Numériques · 0 » ; sous filtre Numériques, zéro **affiché** n'est pas
zéro droit. Puis tester un rafraîchissement forcé avec Paris connecté
et sans source connectée, sans identifiant réel.
