# BnF Prêt — fournisseur HTTPX (#2055)

Depuis la version 0.2.0, le package remplace `ui.page.bnf-pret` par `provider.bnf-pret`, un
`ProviderPlugin` / `ProviderSession` utilisant `httpx.Client`, comme Paris.
L'installation et l'activation ne démarrent aucun navigateur et ne nécessitent
ni Playwright, ni Chromium, ni Browser Recorder. Les anciens modules navigateur sont supprimés.

## Installer et essayer

Depuis la branche contenant cette version :

```bash
python scripts/package-plugin.py src/plugins/provider-bnf-pret --out dist
cifereca-cli plugin install dist/cifereca-plugin-provider-bnf-pret-0.3.1.zip --replace
cifereca-cli plugin enable provider.bnf-pret
```

Dans Configuration, ajouter une connexion **BnF Prêt** au bon profil Cifereca,
saisir l'identifiant et le mot de passe BnF, puis connecter. Les prêts reconnus
sont disponibles dans Emprunts et Synchronisation via les parcours Core habituels.
L'ancien identifiant de page UI n'est plus une contribution du package.

## Authentification et limites connues

Observation publique sans compte du 23 octobre 2026 : activité →
`pret.bnf.fr/sso/saml/init` → `idppub.bnf.fr/idp/profile/…` →
`authentification.bnf.fr/login`. La page de connexion présente un formulaire
POST relatif `login`, champs `username`, `password`, `execution`, `_eventId`,
`geolocation`. Le client lit le formulaire reçu et ses champs cachés ; il ne
reconstruit pas de jeton. Les valeurs de tests sont intégralement synthétiques.

Après soumission des identifiants au seul hôte d'authentification officiel,
le client peut soumettre une réponse SAML fraîchement émise dans cette même
session à l'origine du portail. Aucune assertion issue d'une capture, cookie
importé, export d'état, HTML privé ou persistance SSO n'est utilisé. Une étape
2FA/CAPTCHA/JavaScript ou un formulaire inconnu est refusé avec
`auth_interactive_required` / `unsupported_form`, sans repli navigateur.
**Recette utilisateur du 24/10/2026** : la connexion réelle a réussi et deux
prêts numériques ont été affichés dans « Mes emprunts » (voir #3104). La
synchronisation manuelle est volontairement `partial`, sans clôture par
absence. Cette preuve ne valide pas les dates, couvertures, le GET de jeton
ACSM ni le fonctionnement d'ADEPT sur un véritable prêt.

Les redirections sont contrôlées avant chaque requête. Les POST sont réservés
à la connexion, sans retry ni transfert POST par 307/308. Une lecture de prêts
ne peut pas suivre une redirection SSO, déclencher un emprunt ou télécharger.
Limites : 512 Kio décompressés/document, 12 redirections/appel, 8 étapes de
formulaire, timeout HTTP de 15 s et budget global connexion de 60 s.
Les cookies restent dans le client du credential ; fermeture et échec de
connexion les révoquent. Les erreurs publiques n'exposent que des codes fermés. Le fournisseur publie
également ces codes dans Diagnostics avec la seule phase `login`, `loans`, `search` ou `detail`,
car le Core affiche une erreur générique à la connexion. Aucun identifiant,
URL, cookie, formulaire ou texte de réponse n'est ajouté au diagnostic ; une
panne du collecteur n'altère pas le résultat de l'opération.

## Prêts et synchronisation

Projection HTML de `.active-loans .js-loan`, paire unique de liens
`/resources/{id}` et `/loans/{id}`. Le titre vient du texte du lien de ressource
quand il est unique. Les dates et la pagination des prêts ne sont pas qualifiées :
aucune date n'est inventée. Chaque `Loan` porte provider, credential, compte et
identité exacts. Le lien de téléchargement n'est pas transmis au Core.

L'inventaire est **toujours partiel** (`last_loan_errors` signale
`inventory_not_exhaustive`). La synchronisation ajoute/actualise les prêts
observés, mais une page vide, tronquée, une session expirée ou un prêt absent
ne prouve aucune fin de droit. Le Core actuel (#2975) conserve les anciens droits
sans preuve négative exhaustive ; #2055 interdit les acquisitions BnF implicites.
Aucun fichier, placement ou historique n'est supprimé par ce fournisseur.

La version **0.3.0** ajoute `catalog.search` et `catalog.detail` à `sync.loans`.
Emprunt, réservation, retour et téléchargement ADEPT ne sont pas annoncés.

## Préparation du téléchargement explicite — 0.3.1

Le fournisseur expose uniquement pour intégration future son adaptateur
`download_digital(loan, account, target_dir, explicit_user_action=True)`, **sans déclarer**
`digital_download` ou `acquisition.download` dans ses capacités publiques.
La synchronisation ne l'appelle jamais : seul le parcours CoreUI initié par
l'utilisateur invoque l'adaptateur avec `explicit_user_action=True`. Toute
invocation générique est refusée avant une requête Web. Le garde Core
`bnf_web_explicit_only` reste actif : aucun ACSM n'est obtenu à l'ouverture.

Ce chemin exige une connexion active, l'objet de compte **exact** et un prêt
numérique avec la même identité / connexion / ressource que la dernière page
d'activité observée. Il relit la page d'activité du même compte avant
**un seul GET** de `/loans/{id}`, construit à partir de l'identifiant déjà
observé et non d'une URL arbitraire. Les redirections sont toutes refusées,
sans retry ni POST. Une page vide, un prêt disparu, un autre compte ou une
session fermée bloquent le téléchargement même si le ledger conserve le droit.

Le corps reçu est plafonné à 2 Mio, n'est reconnu comme *candidat ACSM* que
si sa structure XML bornée a la racine Adobe ADEPT `fulfillmentToken` de
type `loan`; les DTD et entités sont refusées. Un `.bin`/MIME ne suffit pas.
Le token validé est sauvegardé dans le dossier de staging Core demandé,
avec un nouveau nom neutre `.acsm` et permissions 0600, sans écraser un
fichier existant. L'URL privée, le corps XML et la référence de prêt ne sont
pas envoyés dans les diagnostics ni retournés comme URL de catalogue.

**Ce n'est pas un fulfillment ADEPT ni une preuve transactionnelle serveur.**
Le parcours CoreUI est disponible depuis #3110 : deux confirmations séparées
(GET du jeton, import ADEPT manuel), contrôle du compte et de l'identité.
Un import réussi reste identifié comme `manual`, sans inventer une attestation
de droit BnF. Le Core revalide indépendamment le jeton et n'accepte pas un
ACSM d'achat ou du XML à entités au seul motif que le fournisseur l'a proposé.
Restent à recetter sur compte réel : réponse effective de `/loans/{id}`,
rattachement/validité du jeton, profil ADEPT d'origine et résultat EPUB/PDF.
Ne pas déduire de cette UI que l'obtention BnF est opérationnelle en réel.

## Catalogue et fiches en lecture seule

Dans le Core, après configuration et connexion du compte BnF, sélectionner
**BnF Prêt** comme source de Recherche. La recherche textuelle générale accepte
les périmètres « Tous » et « Numérique ». Auteur/ISBN comme champs spécialisés,
recherche physique et formats de fichier ne sont pas déclarés. Le client peut
lire les pages publiques sans s'authentifier, mais le Core sélectionne ses
sources parmi les sessions connectées : aucune recherche anonyme dans l'IHM
n'est promise par cette version.

Le GET `/results?q=…` utilise le terme encodé, de 2 à 128 caractères. Le client
suit seulement une ancre visible `rel=next` ou `.pagination a.next`, validée sur
la même origine, les mêmes critères et le numéro suivant. Une recherche lit
au maximum cinq pages dans un budget total de 30 secondes. Aucun numéro de page
n'est reconstruit pour contourner une ancre absente ; l'argument SDK `page`
reste à zéro puisque la pagination est automatique par liens observés.

Les notices sont dédupliquées par ressource, pas par titre. Le titre, l'auteur,
l'éditeur, l'ISBN, la langue et la description proviennent uniquement des
propriétés HTML présentes. Les filtres, hôtes étrangers, liens de prêt/API,
paramètres inconnus et redirections modifiant la requête sont refusés.
Une panne de page ultérieure conserve les notices déjà observées avec un
avertissement ; une panne de première page n'est jamais un catalogue vide réussi.
L'absence de page suivante ne prouve pas l'exhaustivité : le Core reçoit toujours
un avertissement de résultats partiels, et non un faux total ou un zéro exhaustif.

La fiche utilise uniquement `/resources/{id}` du même fournisseur/credential.
Le contrat `ItemDetail` restitue titre, auteur et disponibilité observée. Un
bouton « Emprunter » seul laisse la disponibilité inconnue ; un marqueur
explicite d'indisponibilité peut être restitué. Aucun exemplaire ou droit n'est
fabriqué. Ni la recherche ni la fiche ne déclenchent SSO, emprunt ou téléchargement.
La fermeture invalide les résultats en vol et interrompt la pagination.

**Qualification restante :** les structures DOM et le garde microdata Book sont
couverts par fixtures synthétiques ; aucune recette de catalogue/fiches réels
n'est revendiquée tant que l'accès depuis l'environnement reste refusé.

## Validation

```bash
PYTHONPATH=src python -m pytest src/plugins/provider-bnf-pret/tests -q
python scripts/verify-packaging.py
python -m unittest scripts.test_plugin_packaging
python scripts/package-plugin.py src/plugins/provider-bnf-pret --out dist
```

Tests HTTPX MockTransport : chaîne SSO/formulaires/cookies, inventaire partiel,
compte exact, deux sessions isolées, HTML modifié, erreurs 403/429, taille,
redirection interdite, absence de mutation et fermeture. Les suites Core et
CoreUI couvrent le double consentement, l'immuabilité du prêt choisi et la
validation indépendante de l'ACSM. Ces tests synthétiques ne remplacent ni la
recette du GET réel, ni celle d'ADEPT.
