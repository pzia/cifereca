# Core P1 #1916 — recherche synchrone, génération et identité

La recherche progressive contrôle déjà `_search_generation` et la session
d'identité avant de publier ses résultats. La recherche synchrone historique
`LibraryService.search` manquait cette vérification : un GET fournisseur
pouvait se terminer **après** une sélection d'identité différente puis
réécrire `last_search`, le cache et le catalogue SQLite dans la portée
désormais courante.

La recherche synchrone prend désormais un bail de génération sous le même
verrou Core que la recherche progressive. Les résultats d'une requête
supplantée (changement de profil Alice→Bob, aller-retour Alice→Bob→Alice
ou nouvelle recherche) ne sont **pas publiés** : retour `[]`, sans
modification de `last_search` ni de ses avertissements. Ce contrôle
s'applique également au chemin de cache en lecture. La boucle des fournisseurs et\nles variantes textuelles vérifient le bail **avant chaque GET** : aucune\nseconde tentative de recherche n'est lancée après la révocation de profil.

Le miroir SQLite reçoit toujours **l'identité capturée au départ**, et
jamais le profil sélectionné en fin de GET. Le bail est revérifié avant
l'écriture miroir, avant mise en cache et sous verrou lors de la publication
des résultats. Une transition au cours du miroir peut créer uniquement une
entrée cataloguée sous **l'identité d'origine**, jamais sous la nouvelle ;
elle ne publie aucun résultat UI/cache de l'ancien profil.

Le filtrage des anciennes notices publiques, la séparation des droits privés,
la stratégie de repli stale, l'absence de retry équivalent sur timeout/429/5xx
et le cache fournisseur demeurent inchangés. Aucune requête ni mutation de
droit ajoutée.

Recette de non-régression : GET contrôlé par événements, changements de
profil, ABA, recherche concurrente plus récente, transition pendant le miroir,
hit cache concurrent. Fixtures locales Core/SQLite uniquement, sans réseau.
