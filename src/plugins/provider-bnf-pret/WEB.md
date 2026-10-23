# BnF Prêt — fournisseur HTTPX (#2055)

La version 0.2.0 remplace `ui.page.bnf-pret` par `provider.bnf-pret`, un
`ProviderPlugin` / `ProviderSession` utilisant `httpx.Client`, comme Paris.
L'installation et l'activation ne démarrent aucun navigateur et ne nécessitent
ni Playwright, ni Chromium, ni Browser Recorder. Les anciens modules navigateur sont supprimés.

## Installer et essayer

Depuis la branche contenant cette version :

```bash
python scripts/package-plugin.py src/plugins/provider-bnf-pret --out dist
cifereca-cli plugin install dist/cifereca-plugin-provider-bnf-pret-0.2.0.zip --replace
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
**Connexion authentifiée sur un vrai compte et sélecteurs réels non encore
recettés** : le parcours public et les tests synthétiques ne prouvent pas ce succès.

Les redirections sont contrôlées avant chaque requête. Les POST sont réservés
à la connexion, sans retry ni transfert POST par 307/308. Une lecture de prêts
ne peut pas suivre une redirection SSO, déclencher un emprunt ou télécharger.
Limites : 512 Kio décompressés/document, 12 redirections/appel, 8 étapes de
formulaire, timeout HTTP de 15 s et budget global connexion de 60 s.
Les cookies restent dans le client du credential ; fermeture et échec de
connexion les révoquent. Les erreurs publiques n'exposent que des codes fermés.

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

Cette première tranche déclare seulement `sync.loans`. Recherche, emprunt,
réservation, retour et téléchargement ADEPT ne sont pas annoncés.

## Validation

```bash
PYTHONPATH=src python -m pytest src/plugins/provider-bnf-pret/tests -q
python scripts/verify-packaging.py
python -m unittest scripts.test_plugin_packaging
python scripts/package-plugin.py src/plugins/provider-bnf-pret --out dist
```

Tests HTTPX MockTransport : chaîne SSO/formulaires/cookies, inventaire partiel,
compte exact, deux sessions isolées, HTML modifié, erreurs 403/429, taille,
redirection interdite, absence de mutation et fermeture. Aucun compte réel,
assertion privée, téléchargement ou preuve GTK n'est nécessaire à ces tests.
