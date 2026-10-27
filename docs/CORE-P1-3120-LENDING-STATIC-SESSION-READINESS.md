# Core P1 #3120 — inspection de session sans `__class__` dynamique

`assess_lending_operation()` est une consultation de capacité, non une
admission de mutation. Elle vérifiait déjà les champs mémoire du fournisseur
sans appeler `session.accounts`. La condition `isinstance(session,
ProviderSession)` pouvait cependant déclencher `__class__` via
`__getattribute__` personnalisé pendant le protocole ABC, avec effet de
bord fournisseur.

La validation passe désormais par `type(session)` et la hiérarchie de
classes, puis refuse les sessions qui surchargent `__getattribute__`
avant d'inspecter `__dict__` directement. Sans mémoire vérifiable, la
capacité n'est **pas disponible** et aucun bouton n'est autorisé par cette
observation. Le validateur de publication statique et les fournisseurs
legacy conformes sont inchangés.

Tests : session normale toujours prête, session avec `__getattribute__`
hostile et `__class__` dynamique sans exécution du proxy. Aucun appel
réseau, consentement, retour, acquisition ni mutation de droits.

Le sujet #3120 reste ouvert pour les admissions d'opérations spécifiques,
leurs permissions effectives et les recettes métier. Cela n'implique jamais
que l'existence d'une méthode Python soit une autorisation de prêt.
