# Core P0 #3867 — HTTPS de livraison attestée

**État :** primitive transport Core, sans branchement automatique des adaptateurs fournisseurs. Cette capacité ne prouve **ni consentement**, ni droit, ni publication SQLite.

## Point d’entrée

`HTTPX_TRANSPORT.attested_https_download(producer, url, target, admission_current, headers=None, timeout=20, max_bytes=536870912, max_redirects=8, max_duration=900)`

- `url` : URL de GET HTTPS émise depuis **une offre fraîche, univoque et liée exactement** à un compte, une ressource et un consentement ; jamais une URL déduite d’une fiche publique.
- `target` : chemin de staging **privé et préexistant**, sans symlink, contrôlé par Core. La cible n’existe pas ; elle est créée uniquement après vérification de la réponse, du budget et de l’admission. La primitive ne publie pas de fichier final.
- `admission_current()` : callback Core **obligatoire** retournant le booléen strict `True` uniquement tant que l’identité, le compte, l’offre sélectionnée, le consentement et le contexte restent valables. Réévalué avant le premier GET, avant chaque saut, sur chaque bloc et avant la création du chemin de staging.
- `headers` : en-têtes de l’offre strictement typés ; seuls `Accept`, `Accept-Language`, `User-Agent` traversent une frontière d’origine. Ne jamais transférer l’`Authorization`, le `Cookie`, le `Referer`, l’`Origin`, le CSRF ou les headers spécifiques.
- Sortie `PublicDelivery(path, sha256, size, content_type, redirects)` ; aucun jeton, compte ou URL dans la valeur de retour.

## Garanties de transport

La seule voie HTTP est `HTTPX_TRANSPORT.public_client` : certificat TLS pour le hostname demandé, pair TCP public épinglé après résolution DNS, aucun proxy d’environnement, aucune liste statique d’hébergeurs. Chaque saut 301/302/303/307/308 est validé **avant** le prochain GET ; HTTPS obligatoire, absence de downgrade, IP littérale, userinfo, fragment, autorité ambiguë ou caractères de contrôle. Boucles et nombre de sauts bornés.

La réponse utile doit être un 200 non HTML. Le streaming est borné en octets **décodés** et en durée totale, en plus du timeout du client. L’écriture 0600 se fait sous un répertoire ouvert composante par composante avec `O_NOFOLLOW` ; le chemin final de staging est créé par hardlink sans écrasement, fsync et validation des inodes source/cible. Toute erreur avant publication nettoie le temporaire ; une cible existante n’est jamais remplacée.

Les erreurs HTTPX, de chemin, d’URL, de consentement, de limite et de réponse reçoivent des codes fermés sans URL signée ni données personnelles : `delivery_transport_unavailable`, `delivery_redirect_invalid`, `delivery_staging_unavailable`, `delivery_admission_changed`, `delivery_deadline`, `delivery_size_exceeded`, etc. Aucun texte d’exception réseau ne doit être journalisé tel quel.

## Frontière d’intégration

1. **Provider** : observe la ressource, atteste l’offre et transmet le candidat d’URL (métadonnées et portée exactes).
2. **Core** : décide de l’admission (SourceKey, session, compte, consentement, état sélectionné), puis peut invoquer ce transport sous le verrou d’acquisition existant.
3. **Core** : valide la réponse complète et le format EPUB/ACSM/ADEPT via son pipeline existant ; vérifie le SHA et la provenance, publie `Artifact / Placement / OriginLink`, puis indexe le livre. Le transport seul **ne conclut jamais** à `present_attested`.
4. **Plugins/Agent B** : raccorder Numilog, Kobo, Paris et BnF-Prêt sans modifier les règles de POST authentifié, SSO, Vault ou recherche de catalogue ; éprouver les 302/307/308 vers un hébergeur tiers et les tests de session/compte révoqués.

**Non-critère de clôture :** une CI de cette primitive ne suffit pas à clôturer #3867. Il faut vérifier l’utilisation effective par les fournisseurs concernés et une recette réseau réelle.
