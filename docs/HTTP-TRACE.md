# HTTP Trace — contrat Core #3203

Le niveau effectif `trace` existant active les observations des transports
explicitement instrumentés. Aucun état ou préférence de capture supplémentaire.
`verbose`, `normal` et `errors` ne capturent rien. Les clients déjà construits
avec ces transports suivent également les changements du niveau effectif.

## Service HTTPX obligatoire pour les plugins

Décision utilisateur du 26/10/2026, #3315 / BnF-Prêt P0 #3316 : tout plugin
construit ses clients avec le service Core public `HTTPX_TRANSPORT.client()` ou
`HTTPX_TRANSPORT.async_client()`, également disponible via le broker `httpx`.
Chaque appel demande un `producer` explicite et conserve les options natives
HTTPX. Chaque client possède ses cookies, authentification, limites et pools ;
le service commun ne partage jamais une session de compte entre identités.
Le tampon Trace appartient au Core ; aucun paramètre de capture autonome n'est
accepté par le service. Les clients sont fermés par leur propriétaire habituel.
HTTPX reste utilisable pour les types, exceptions et transports de tests.
Les wrappers/classes ci-dessous sont les primitives internes du service et un
pont de migration, pas une autorisation de pile réseau autonome dans un plugin.

### Registre des exceptions

| Plugin | Exception admise | Justification / portée |
| --- | --- | --- |
| Browser Recorder | Reste autonome | Navigation, capture et rejeu supervisé pilotés par son navigateur ; aucune greffe du transport partagé sur ce parcours. |

Aucune autre exception n'est autorisée. Les chemins non encore migrés sont une
dette suivie par #3315, pas de nouvelles exceptions implicites. BnF-Prêt est P0
(#3316), les autres adaptations sont P1. La capture Trace #3203 reste P0.

## User-Agent HTTP Core par défaut (#3838)

Tous les clients créés par `HTTPX_TRANSPORT.client()`, `async_client()` ou
`public_client()`, ainsi que les wrappers Core `TraceHTTPClient` et
`TraceAsyncHTTPClient`, reçoivent par défaut le même
`BROWSER_USER_AGENT` (défini dans `cifereca.http_trace`). Ce navigateur
standard remplace l'identité générique HTTPX ; le Core n'impose aucun autre
en-tête (Accept, Origin, Referer, cookies, authentification ou compression).
Le profil est déterministe et n'est pas un mécanisme de contournement de
contrôles anti-bot. Les appels `urllib.request` contrôlés du service LLM et
de vérification PyPI utilisent la même constante, sans changement de leur
politique de TLS, proxy, redirection ni de limites.

Les `headers` fournis par l'appelant gardent leurs valeurs et leur casse
logique HTTPX, y compris `User-Agent` lorsqu'il est **explicitement**
nécessaire au contrat d'un service. Une requête peut également apporter son
propre `User-Agent` si son protocole l'exige ; ce n'est pas le défaut du
Core. Exception attestée : Project Gutenberg exige un identifiant avec
contact configuré pour son accès OPDS et pour la livraison associée. Les
scripts de développement/CI autonomes ne passent pas par le service HTTP
applicatif. Les navigateurs réels du plugin Browser Recorder conservent
leur propre identité. Toute nouvelle exception doit être documentée et testée.

La politique ne modifie ni les domaines autorisés, ni les contrôles des
redirections, ni l'isolation des cookies et credentials entre origines ;
aucun monkeypatch global et aucun partage de session n'est ajouté. Les
clients HTTP tiers qui ne sont pas créés par le Core restent inchangés.

## Adaptation d'un client HTTPX

```python
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

client = HTTPX_TRANSPORT.client(
    producer="provider.example",
    trust_env=False,
    follow_redirects=False,
)
```

Le wrapper peut recevoir un transport existant en premier argument. Reporter
sur le transport les options réseau déjà utilisées (`verify`, `cert`, `limits`,
`http1`, `http2`, `retries`, `proxy`, `local_address`, `uds`, `trust_env`), sans
changer la politique du client. En présence de `mounts` ou de proxies gérés par
HTTPX, envelopper **chaque transport réellement sélectionné**, sinon leur trafic
reste hors périmètre. Les wrappers n'interceptent pas les autres clients, les
modules `requests`/`urllib`, les sous-processus ADEPT ou les navigateurs.
Aucun monkeypatch global ; `request()` et `send()` passent par le même transport.

La consommation des flux et la fermeture restent pilotées par HTTPX et le métier.
La capture ne lit pas un flux en avance. Les corps observés au transport sont les
octets **avant la décompression HTTPX**, distincts du contenu retourné au métier.
Une réponse déjà matérialisée par un transport tiers est explicitement marquée
`httpx_buffered_content` : ses octets réseau d'origine ne sont pas revendiqués.
Les en-têtes observables conservent ordre, casse et doublons en paires Latin-1
réversibles. HTTPX/HTTPcore peuvent normaliser certains détails du protocole : ce
format n'est pas une capture de paquets ni une preuve d'octets TLS ou HTTP bruts.
Un corps de requête non consommé par le transport reste `not_read`, même si le
client l'avait préconstruit. Aucun octet envoyé n'est inventé dans ce cas.

## Clients avec proxies ou montages

`TraceHTTPClient` et `TraceAsyncHTTPClient` sont également publics en Plugin API
1.4.1 et acceptent les options natives de `httpx.Client` / `AsyncClient`, plus
`producer`. Utiliser ces classes pour préserver automatiquement la sélection
native des proxies d'environnement, `NO_PROXY`, montages et options SSL. Aucune
reconstruction d'options, modification globale ou lecture eager. Le seul point
de dispatch interne surchargé est `_transport_for_url`, couvert par les tests
et limité à la dépendance HTTPX 0.28.x épinglée par le Core. Tout futur upgrade
HTTPX doit revérifier ce contrat avant de changer la borne de dépendance.

Un transport déjà instrumenté n'est pas enveloppé une seconde fois. Le client
natif reste propriétaire de la fermeture des pools/montages. Le chargeur de
couvertures Core utilise ce client et garde intégralement ses limites de pool,
son routage et ses préférences HTTP/SSL/proxy précédentes. Les clients externes
ordinaires restent hors périmètre tant qu'ils ne sont pas adaptés explicitement.

## Budgets et états

Par défaut : 2 Mio par corps, 32 Mio cumulés de métadonnées et corps stockés,
1 000 échanges, 64 Kio de métadonnées par côté. Le nombre d'entrées et les données
sont bornés sous verrou commun pour tous les workers sync/async. Les erreurs de
capture ne changent jamais le résultat ou l'exception du transport métier.

Chaque corps indique `not_read`, `partial`, `complete`, `quota`, `trace_disabled`
ou `error`. `observed_bytes` distingue le volume vu du volume stocké. Un flux
fermé sans consommation n'est pas complet. Un abandon après lecture partielle
reste partiel. Une saturation ne coupe pas la réponse métier ; les échanges
omis ont un compteur global explicite. Une bascule hors Trace cesse la collecte
des corps et métadonnées supplémentaires ; les constats de terminaison des
échanges déjà ouverts ne contiennent pas de nouveau corps ni de texte d'erreur.
La reprise de Trace ne complète jamais rétroactivement un corps interrompu.

## Session et export privé

Le tampon est exclusivement en mémoire du processus : pas d'archive interne
persistante, pas de restauration après crash, pas d'historique inter-sessions,
pas de dossier à purger ou de capture d'une autre instance à supprimer. Chaque
nouveau processus reçoit un identifiant de session aléatoire et un tampon vide.
Les exports explicitement choisis sont des copies durables indépendantes.

`DiagnosticsBroker.http_trace_status()` fournit seulement les agrégats.
`export_http_trace(destination)` exige Trace actif et publie un **nouveau** ZIP
atomiquement, en permissions `0600`, sans remplacer une destination existante
ou suivre son symlink. L'appelant doit proposer la confirmation d'export brut et
le sélecteur de fichier. Une erreur d'écriture laisse la destination absente,
les temporaires sont retirés. Aucun upload automatique.

Le ZIP contient `index.json` (`cifereca.http-trace/1`) et `bodies/*.bin`.
Chaque corps référence son blob, sa taille stockée et son SHA256 ; les octets et
métadonnées ne sont pas anonymisés. Les secrets éventuels doivent rester privés.
Seul un `task_ref` obtenu dans un vrai contexte de tâche existant est ajouté ;
pas de tâche ou `operation_ref` fabriqué ni de corrélation par horodatage.
Les corps ne sont pas dupliqués dans le journal canonique.

Cette première tranche fournit les primitives et le broker. L'adoption des
clients Core/plugins et le bouton Diagnostics nécessitent leurs adaptations
respectives : cette API seule ne prétend pas couvrir leurs échanges existants.

## Destinations publiques avec DNS épinglé — API 1.4.2 (#3335)

`HTTPX_TRANSPORT.public_client(producer=..., timeout=...)` utilise le même
Trace Core mais impose une connexion directe (pas de proxy, mount, transport
ou SSL remplaçable), un timeout fini positif, HTTP/1.1 et aucun retry/redirect
implicite. Tous les résultats DNS doivent être globaux ; l'adresse numérique
choisie est connectée sans seconde résolution du hostname, puis le pair est
contrôlé avant d'envoyer HTTP. HTTPcore garde l'origine initiale pour Host, SNI
et vérification du certificat TLS, avec le contexte système vérifié.

Ce contrat reprend le transport public déjà employé par les flux de Critique
littéraire : sa migration ne doit pas affaiblir l'épinglage ni ajouter une
exception autonome. Le caller conserve la whitelist d'URL, les limites de
corps, le budget global et les décisions de redirection (dont refus de
downgrade HTTPS). Le backend synchrone dépend explicitement de HTTPcore 1.0.x
et HTTPX 0.28.x ; l'upgrade de ces bornes doit rééprouver ce point d'intégration.
Aucune requête de sondage et aucune interception globale ne sont ajoutées.

## Garde de frontière CI (#3315)

`scripts/ci_plugin_httpx_boundary.py` contrôle les imports et constructeurs
HTTP connus de tous les fichiers Python de production des plugins. Le check
PR CI l'exécute même si la modification n'ajoute pas de dépendance Python.
Les types/erreurs HTTPX et `urllib.parse` restent autorisés ; constructeurs
HTTPX directs, raccourcis réseau et piles urllib.request/requests/aiohttp/
http.client sont refusés, y compris les alias simples et `getattr` littéral.
Les doubles sous `tests/` sont exclus. Browser Recorder est la seule exception
de production, par nom de package exact. Cette garde statique ne constitue pas
une sandbox ; les tests de consommateurs prouvent aussi le passage réel par le
service et la fidélité des échanges capturés.
