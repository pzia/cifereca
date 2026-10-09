# Qualification HTTPS Jeunesse — P1 #741

Cette recette est une opération locale, explicite et ponctuelle. Elle ne se lance
pas en CI et ne contacte aucun site sans --confirm-live. Elle constitue une
preuve complémentaire aux fixtures et aux captures GTK déjà intégrées.

## Préparation

Depuis un checkout vérifié de pzia/cifereca, sur une machine disposant
d'Internet et d'un environnement Python >= 3.11 :

    python3 -m venv /tmp/cifereca-youth-check
    /tmp/cifereca-youth-check/bin/python -m pip install -e .

Le script crée un HOME et des chemins XDG dans un répertoire temporaire,
puis installe les véritables packages jeunesse et la source dans le store
Cifereca isolé avec core.plugins.install(). Il utilise le transport HTTPS
httpx de production, sans MockTransport, proxy d'environnement ou redirection.
Aucune identité ni aucune bibliothèque personnelle ne sont utilisées.

## Exécution bornée

Sans consentement explicite, le script ne réalise aucune installation
ni requête et retourne NOT_RUN :

    /tmp/cifereca-youth-check/bin/python scripts/qualify-youth-source-live.py --source booktrust

BookTrust, une seule requête HTTPS maximale :

    /tmp/cifereca-youth-check/bin/python scripts/qualify-youth-source-live.py --source booktrust --age 8 --limit 2 --confirm-live > booktrust-live.json

Milan, jusqu'à une page et huit fiches (neuf requêtes HTTPS maximales) :

    /tmp/cifereca-youth-check/bin/python scripts/qualify-youth-source-live.py --source milan --age 8 --limit 2 --confirm-live > milan-live.json

Les paramètres imposent un âge 0–17, un nombre de notices demandé 1–3 et un
timeout 2–15 secondes. Aucune page suivante n'est parcourue automatiquement.

## Interprétation des résultats

Le JSON inclut UTC, SHA Git, version Core, identifiant du plugin réellement
installé, URLs publiques consultées, décompte des requêtes et des notices,
titres, ISBN si présent, tranches d'âge explicitement publiées, provenance et
erreurs éventuelles. Aucun jeton, compte utilisateur ou cache permanent.

- PASS : au moins une notice avec âge et provenance explicitement attestés,
  après ingestion dans le vrai catalogue et lecture du vrai cache.
- NO_VERIFIED_NOTICE : pas de notice répondant aux exigences ; aucune réussite.
- BLOCKED : échec réseau, du plugin ou de l'installation ; aucune réussite.
- NOT_RUN : l'accord --confirm-live est absent ; aucune requête.
- INVALID_INPUT : paramètres hors bornes ; aucune requête.

Une réponse HTTP 403, 429 ou un timeout ne justifie ni contournement, ni
scraping agressif, ni répétition automatique. Conserver la trace JSON dans
le ticket #741. L'absence d'accès depuis un environnement ne prouve pas
une panne du site. Une page consultable par navigateur ne prouve pas
l'acceptation d'une notice par le plugin.

Ce test ne valide pas le rendu GTK ni l'ouverture du navigateur (rôle UX
indépendant, #758). Les recettes de pannes 503 restent déterministes en CI ;
ne pas provoquer de pannes sur les sites distants. Ne pas fermer #741
sur un seul résultat sans validation des autres critères de complétude.
