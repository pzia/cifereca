# Qualification HTTPS Jeunesse — P1 #741

Cette recette est une opération explicite et ponctuelle : locale ou via
un workflow GitHub Actions **manuel uniquement**, jamais automatique.
Elle ne contacte aucun site sans accord explicite (--confirm-live en local,
confirmation dédiée dans GitHub Actions). Elle complète les fixtures et
les captures GTK déjà intégrées.

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

    /tmp/cifereca-youth-check/bin/python scripts/qualify-youth-source-live.py --source milan

Milan, jusqu'à une page et huit fiches (neuf requêtes HTTPS maximales) :

    /tmp/cifereca-youth-check/bin/python scripts/qualify-youth-source-live.py --source milan --age 8 --limit 2 --confirm-live > milan-live.json

**l'école des loisirs**, une unique requête HTTPS publique vers
`/recherche-avancee` avec `types=livre` et le mot-clé fixe `loup` :

    /tmp/cifereca-youth-check/bin/python scripts/qualify-youth-source-live.py --source ecole-loisirs --age 3 --limit 2 --confirm-live > ecole-loisirs-live.json

Le vrai plugin `youth.source.ecole-loisirs` est installé avec le vrai
`YouthCatalogPlugin` dans une identité et un store XDG éphémères.
La réponse de **la première page seulement** est analysée : âge publié,
ISBN valide et notice canonique; filtrage des 3 ans uniquement **en cache**.
Aucune requête à une fiche produit ni à une page supplémentaire, même si
le site annonce plusieurs résultats. Un résultat `PASS` exige une
requête HTTPS réellement observée **et** une notice sourcée après ingestion.
En cas de refus ou d'absence de notice, garder le JSON `BLOCKED` ou
`NO_VERIFIED_NOTICE` sans second essai automatique.

Les paramètres imposent un âge 0–17, un nombre de notices demandé 1–3 et un
timeout 2–15 secondes. Aucune page suivante n'est parcourue automatiquement.

## Alternative si le poste local n'a pas d'accès DNS/HTTPS

Le workflow [Qualify youth public HTTPS (manual)](../.github/workflows/youth-source-live-qualification.yml)
est exclusivement `workflow_dispatch` : aucune PR, poussée sur `main` ou
planification ne déclenche de requête extérieure. Sur le dépôt GitHub :

1. Ouvrir **Actions → Qualify youth public HTTPS (manual) → Run workflow**,
   en sélectionnant **main** puis une seule source (`milan` ou `ecole-loisirs`).
2. Après décision explicite de contacter ce site public, cocher
   `I authorize a bounded live HTTPS request...`. Sans la case, le job est
   ignoré. Ne pas utiliser ce mode pour tester les fixtures.
3. Consulter le résumé de l'exécution et télécharger l'artefact JSON
   `youth-source-live-evidence` (conservé 7 jours), puis joindre la preuve
   pertinente à [#741](https://github.com/pzia/cifereca/issues/741).

Le job dispose d'un délai maximal de **4 minutes**, d'un seul fournisseur,
d'un **maximum de 1 requête École des loisirs ou 9 Milan** via les bornes du script,
sans proxy ni redirection. Un résultat `BLOCKED` ou `NO_VERIFIED_NOTICE`
ne prouve pas un succès. **Ne pas relancer à répétition**, surtout après
403/429/timeout : cela consomme le budget Actions et peut solliciter
inutilement le site. Un verdict `PASS` reste à confirmer depuis le JSON ;
ce workflow n'est ni un check requis ni une release.

## Interprétation des résultats

Le JSON **publié** (résumé Actions et artefact) applique désormais une
**liste blanche fermée** : état de qualification, source/hôte public autorisé,
date/version/SHA formatés, décomptes de requêtes, d'ingestion, d'erreurs et de
notices qualifiées, indicateurs booléens, code d'erreur énuméré et diagnostic
structurel borné. Les titres, ISBN, notices, URL complètes (requêtes ou
produits), recherches, libellés libres, messages d'exception et chemins privés
ne sont **jamais exportés**. Les preuves bibliographiques détaillées sont
utilisées seulement en mémoire pour calculer le verdict.

Le workflow capture la sortie brute du script hors des artefacts, la valide
séparément puis ne publie que le rapport filtré. Toute sortie non JSON,
source incohérente ou déclaration de succès sans requête et notice qualifiée
devient un résultat `BLOCKED / invalid_evidence`, sans reprise automatique.
Les journaux bruts de la qualification ne sont pas exposés dans Actions.

### Diagnostic Milan embarqué dans le JSON (depuis le correctif P1)

Le résultat du workflow manuel intègre `plugin_diagnostic` lorsqu'un
adaptateur Milan instrumenté a réellement exécuté la collecte. L'information
provient de `MilanYouthSource.diagnostic_snapshot()`, également publiée dans
la page **Diagnostics** via l'API publique `PluginContext.report_diagnostic`.

Ce diagnostic **reste disponible dans le JSON même si le plugin échoue**,
car il est collecté dans le bloc `finally` avant suppression du profil
temporaire. Il indique notamment `http_status`, `html_bytes`,
`html_sha256_prefix`, `anchor_count`, `milan_catalogue_href_count`,
`product_href_count`, `product_link_accessible_count`,
`unrecognized_catalogue_href_count`, `next_cursor_present`,
`detail_requests` et `phase`. Une liste blanche de types et de champs
interdit l'inclusion de HTML brut, de cookies, d'URL, de titre ou de secrets.

### Diagnostic École des loisirs dans le JSON

À partir de cette recette, la source `ecole-loisirs` exporte aussi dans
`plugin_diagnostic` son **dernier état local** (`diagnostic_snapshot()`)
sans aucun appel HTTPS supplémentaire, y compris si `search()` échoue.
Seuls les compteurs bornés (`http_status`, `html_bytes`,
`request_count`, `anchor_count`, `product_url_count`,
`returned_items`, `qualified_age_count`,
`unqualified_age_count`, `duration_ms`), les indicateurs booléens
(`content_type_is_html`, `text_filter_present`,
`next_cursor_present`), les états explicitement connus,
l'empreinte HTML SHA256 tronquée et un type d'exception issu d'une liste
fermée sont admis.
Aucun terme recherché, URL supplémentaire, titre, en-tête, cookie ou HTML
brut n'est exporté. `http_status=200` avec zéro notice ne vaut **pas**
`PASS` : le verdict conserve les règles strictes du catalogue.

Si `plugin_diagnostic` est absent, vérifier que le run a été **créé**
sur le commit actuel de `main`, et non relancé depuis une vieille tentative
via « Re-run jobs ». Son absence ne prouve rien sur le site.
Aucun appel HTTP supplémentaire n'est effectué pour constituer ce rapport.
L'export du diagnostic ne transforme jamais un `BLOCKED` en `PASS`.

- PASS : au moins une requête HTTPS publique observée et au moins une notice
  avec âge et provenance explicites de la source interrogée, après ingestion
  dans le vrai catalogue et lecture du vrai cache.
- NO_VERIFIED_NOTICE : pas de notice répondant aux exigences ; aucune réussite.
- BLOCKED : échec réseau, du plugin ou de l'installation, ou aucune
  requête HTTPS réellement observée ; aucune réussite.
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
