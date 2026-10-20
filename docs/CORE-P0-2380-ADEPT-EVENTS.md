# #2380 — faits techniques du runtime ADEPT, contrat Core v1

Le gestionnaire Core atteste l'identifiant installé `runtime.kobo-adept` **et**
le package `org.cifereca.runtime.kobo-adept` avant de publier le callback.
Un champ `plugin_id` dans le détail d'un autre plugin ne donne aucune preuve.
Le producteur interne `Diagnostics.add_core_kobo_adept` réserve son marqueur ;
le générique `Diagnostics.add` ne peut pas attester un événement ADEPT.
Cette vérification est une provenance de routage, pas une signature de code
ni un bac à sable contre un plugin Python hostile dans le même processus.

| Donnée | Validation / provenance | GUI via events, diagnostics.events, export volontaire | Log de cette émission | CI / IA distante |
| --- | --- | --- | --- | --- |
| `schema_version=1`, `plugin_id`, `operation_kind=kobo_adept`, `phase=completed` | Producteur Core réservé | Oui | Oui | Même filtrage, transmission explicite uniquement |
| `operation` | Famille de commande fermée ; `unknown` sinon | Oui | Oui | Idem |
| `status`, `code` | Succès `completed` ou code d'échec classifié et fermé | Oui | Oui | Idem |
| `retryable` | Booléen exact rapporté par le runtime, seulement avec un code connu | Si attesté | Si attesté | Idem |
| Durée, taille stdout/stderr, code retour | Entiers bornés ; sortie non nulle interdite avec succès | Si observés | Si observés | Idem |
| `task_ref` | Contexte réel du worker Core, jamais détail fourni par le runtime | Si contexte disponible | Si contexte disponible | Idem |
| Fournisseur d'acquisition, identité, compte, profil, titre, chemin, URL, exception, stdout/stderr et leurs résumés | Non attestés ou privés | Interdit | Interdit | Interdit |

Le schéma appartient au Core dans `core/diagnostic_adept.py`. Il admet les
codes déjà publiés par le classifieur du runtime : erreur ADEPT opaque,
licence déjà activée par une autre identité, erreurs de profil et codes
fermés de transport/lancement/JSON. Il ne requalifie pas le contrat du CLI
amont et n'invente pas de cause Adobe. Un code non reconnu devient
`code_non_disponible` et ne permet pas de publier `retryable`. L'indication de
réessai est une observation ; aucun retry ni mutation n'est déclenché.

Le callback texte et les résumés de processus ne sont jamais copiés. Le Core
construit une structure validée unique, puis l'émet dans Diagnostics et le
logger existant `cifereca`. La lecture du journal n'accepte que le préfixe
exact `TRACE adept runtime ` et son JSON canonique de 2 048 caractères au
maximum, revalidé entièrement : pas de suffixe, clé dupliquée ou champ libre.
Les messages et détails publiés sont identiques entre sources ; leurs
`event_id` sont indépendants. Les tailles d'octets ne contiennent aucun octet
du processus. Les appels portant l'identifiant réservé mais issus d'un
package non attesté produisent seulement un diagnostic fermé de provenance
indisponible, sans repli vers le contenu libre.

Le callback actuel décrit uniquement un résultat terminal de commande. Il
ne prouve pas de phase intermédiaire et n'ajoute pas de fausse référence de
commande. Plusieurs commandes d'un même worker partagent son `task_ref` ;
hors worker la référence manque. Aucun fournisseur Kobo/Paris/Numilog n'est
inféré du nom du runtime. La vue par tâche lie des observations, pas des causes.

Les niveaux et plafonds existants restent applicables : collecteur mémoire
500 entrées par défaut, succès détaillés filtrables ; log selon niveau courant
et rotation ; lecteur limité à quatre fichiers, 32 Kio et 200 lignes chacun.
La collecte et le log peuvent être incomplets indépendamment. Pas de nouveau
collecteur, upload, activation DEBUG ou rétention supplémentaire. Considérer
le risque de corrélation entre opérations avant de partager une référence ;
les frontières et consentements propres à la copie et à l'IA restent actifs.

Preuves synthétiques : vrai PluginManager, package de test installé et callback
lié au contexte ; vrai reporter du runtime en test, worker et deux canaux,
erreur connue/inconnue, faux package/plugin, valeurs hostiles, refus des
contenus de processus et JSON malformé. Les tests du classifieur runtime sont
vérifiés séparément. Aucun compte Adobe, fulfillment ni recette terrain réelle.
Les autres producteurs et exports génériques restent suivis dans #2380.
