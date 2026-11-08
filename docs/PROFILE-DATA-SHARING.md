# Règle produit — données partagées entre profils

Décision explicite du propriétaire, 8 novembre 2026. Références : #2385, #3104, #4761, #4662, #4987 et #4988.

## Invariant : la sélection du profil n'est pas une ACL de lecture

**Toutes les données métier de tous les profils sont consultables par tous les utilisateurs de l'installation Cifereca.** Le profil actif est un filtre et un contexte ergonomique, non une frontière de confidentialité.

Les notices et catalogues, les métadonnées, les vignettes, les bibliothèques, les possessions, les achats, les prêts observés et leur provenance ne doivent **jamais être cachés au seul motif que leur profil source est différent**. Les résultats vus depuis A peuvent être sourcés depuis B : afficher leur propriétaire/provenance exacts plutôt que prétendre que les biens ou droits appartiennent à A. Une liste filtrée par profil peut rester volontairement filtrée, mais une vue globale ou inter-profils ne doit pas être empêchée par une interdiction de lecture.

Une projection `shared_read_only` signale l'absence de délégation **d'opérations**, pas la confidentialité des données. L'ancienne convention « tout champ étranger est privé » est obsolète. Les projections et leurs caches doivent préserver l'origine réelle (source, credential référencé et profil propriétaire), sans changer silencieusement le propriétaire.

## Frontière technique indépendante : secrets et opérations

Les mots de passe, cookies, jetons d'accès, clés de licence/DRM et paramètres d'URL qui valent **capabilité d'authentification ou de livraison** restent des secrets, non des métadonnées à publier. Leur consultation/usage, si nécessaire, passe par les services et handles Core autorisés, jamais par des logs, labels ou exports bruts. Cette règle n'interdit pas l'accès aux **données** que ces références permettent de lire ; elle impose de ne pas exposer la clé d'accès elle-même.

Toute opération mutatrice auprès d'un fournisseur (emprunt, achat, réservation, retour, téléchargement autorisé, retrait) doit conserver l'identité du **compte fournisseur effectivement utilisé**, l'offre/l'édition exacte et la confirmation requise. Voir le prêt ou le livre de B ne signifie pas « prêt d'A ». L'emprunt par A et la copie vers B sont deux opérations indépendantes (#4987/#4988) ; aucun transfert implicite de droits, de clés ou de licence.

## Conséquences d'implémentation et de recette

- **Recherche/Recommandations** : utiliser le profil global Core pour les calculs contextuels (PR #5040). Un résultat partagé peut conserver les métadonnées et une couverture de B dans une vue A, même si A n'a pas de connexion à ce fournisseur. Ne pas confondre cache par profil, contexte de sélection et confidentialité.
- **Paris** : PR #5042 conserve désormais les couvertures du catalogue admissibles entre profils. Cela ne constitue **pas** une preuve que toutes les images authentifiées ni tous les champs de lecture sont visibles ; #3104 reste le ticket de recette et de généralisation.
- **Tests** : A (sans fournisseur Paris) et B (avec Paris), requête catalogue A avec origine B, affichage image et provenance, recherche/recommandations, bascule A→B→A, cache froid/chaud, absence de fuite de secret, aucune mutation au rendu ; vérifier séparément les actions explicites sur le compte réellement propriétaire.
- Une règle de refus fondée uniquement sur `source_identity != active_identity` dans un chemin **de lecture** doit être revue ; ne pas supprimer mécaniquement les gardes qui portent sur la mutation, l'authentification ou les droits fournisseur.

Ce document est la règle produit de référence. Les décisions de segmentation par défaut doivent l'appliquer ; toute exception doit être documentée par un motif autre que « autre profil ».
