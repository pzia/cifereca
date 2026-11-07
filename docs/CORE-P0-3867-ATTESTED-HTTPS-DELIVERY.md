# Core P0 #3867 — HTTPS de livraison attestée

**État :** primitive transport Core, sans branchement automatique des adaptateurs fournisseurs. Cette capacité ne prouve **ni consentement**, ni droit, ni publication SQLite.

## Point d’entrée

`HTTPX_TRANSPORT.attested_https_download(producer, url, target, admission_current, headers=None, timeout=20, max_bytes=536870912, max_redirects=8, max_duration=900, on_url_refusal=None, on_trace_url=None)`

- `url` : URL de GET HTTPS émise depuis **une offre fraîche, univoque et liée exactement** à un compte, une ressource et un consentement ; jamais une URL déduite d’une fiche publique.
- `target` : chemin de staging **privé et préexistant**, sans symlink, contrôlé par Core. La cible n’existe pas ; elle est créée uniquement après vérification de la réponse, du budget et de l’admission. La primitive ne publie pas de fichier final.
- `admission_current()` : callback Core **obligatoire** retournant le booléen strict `True` uniquement tant que l’identité, le compte, l’offre sélectionnée, le consentement et le contexte restent valables. Réévalué avant le premier GET, avant chaque saut, sur chaque bloc et avant la création du chemin de staging.
- `headers` : en-têtes de l’offre strictement typés ; seuls `Accept`, `Accept-Language`, `User-Agent` traversent normalement une frontière d’origine. **Exception contrôlée Kobo/S3** : `x-amz-request-payer` traverse seulement lorsque sa valeur est exactement `requester` (constante non authentifiante, attestée par le candidat Kobo). Une valeur différente, un autre header spécifique ou un secret ne traverse jamais. Ne jamais transférer `Authorization`, `Cookie`, `Referer`, `Origin`, CSRF ou les jetons AWS d’authentification.
- `on_url_refusal(phase, reason)` : **facultatif**, observabilité P1 #4882. Quand la validation HTTPS refuse une URL, le Core transmet uniquement `phase=initial|redirect` et un **motif de syntaxe fermé** (p. ex. `fragment_marker`, `scheme_not_https`, `parser_disagreement`). L'observateur ne reçoit jamais l'URL, le domaine, un jeton, le corps HTTP ou une exception brute. Son échec est ignoré ; son retour **ne peut pas** changer l'autorisation ni la décision du transport. Les clients existants ne passent pas cet argument et leur comportement reste inchangé. Le plugin Kobo applique une seconde whitelist aux deux valeurs avant le log local `EVENT kobo candidate_http_url_refusal`. Le code d'échec public demeure `kobo_candidate_redirect_refused`.
- `on_trace_url(phase, url)` : observateur **facultatif**, exclusivement sous le niveau diagnostic effectif `trace`, décidé par `HTTPX_TRANSPORT.trace_enabled()` côté Core. Reçoit l'URL complète initiale ou l'URL de redirection après `urljoin`, **avant** son éventuel GET, même si le validateur la refuse. L'observateur n'ajoute aucun appel HTTP, ne peut pas autoriser une destination ni influer sur l'issue ; son échec est ignoré. En `normal` et `verbose`, **aucune URL n'est transmise**. Les URL sont des **secrets de capacité** potentiels (query signée) : seul le fournisseur Kobo les journalise localement en entrée `TRACE` sous le mode utilisateur explicite. Pas de payload d'authentification KVault ni de copie vers un issue ou une CI.
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
