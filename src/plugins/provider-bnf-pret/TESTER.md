# Tester BnF Prêt Web maintenant (P0 #2055)

**Disponibilité :** `ui.page.bnf-pret` est une page de **lecture Web expérimentale**,
désactivée par défaut et utilisable sur un poste graphique Linux disposant
du Core Cifereca compatible et de Playwright/Chromium. C'est un lecteur Web,
**pas encore** un provider `catalog.search`/`sync.loans` ni un client API
institutionnelle. Aucune demande de prêt, restitution, capture ou récupération
de fichier ne doit partir de ce test.

## 1. Installer en une commande depuis le dépôt

Fermer l'application GTK, mettre à jour la branche `main`, puis :

```bash
bash scripts/test-bnf-pret-local.sh
```

Le script construit le ZIP avec `scripts/package-plugin.py`, puis appelle
`cifereca-cli plugin install ... --replace`,
`cifereca-cli plugin enable ui.page.bnf-pret` et
`cifereca-cli plugin diagnose ui.page.bnf-pret`.
L'installation/activation sont volontaires ; aucune session Chromium ni SSO
n'est lancée par ce script. `--replace` est destiné au **même package/version**
en développement. Les autres packages et leur état ne sont pas modifiés.
Ne pas exécuter le script sous `sudo`.

Pour **construire seulement** l'archive installable, sans toucher au profil
Cifereca :

```bash
bash scripts/test-bnf-pret-local.sh --package-only
```

Cela produit `dist/cifereca-plugin-provider-bnf-pret-0.1.0.zip` à la version
actuelle du manifeste (le script suit une future mise à jour de version).
Dans l'écran **Plugins → Installer un ZIP**, sélectionner ce fichier,
puis activer `BnF Prêt (lecteur Web expérimental)` si nécessaire.

Si `python3` ne possède pas le module `packaging`, choisir un Python de
développement avec ce module via `CIFERECA_BNF_PYTHON=/chemin/python`.
`cifereca-cli` doit désigner le **Core effectivement utilisé par l'application**,
pas un autre venv. Un Core incompatible refusera le ZIP sans l'activer.

## 2. Vérifier Playwright et Chromium

L'installation du ZIP / de la dépendance Python **n'installe pas le navigateur**.
Dans **Plugins**, ouvrir la fiche **Browser Recorder**, qui dispose des boutons
de préparation partagés : **Installer les dépendances Python…** (si demandé),
**Installer Chromium…** puis **Vérifier Chromium…**. Ces étapes exigent des
consentements séparés ; l'installation du navigateur peut nécessiter plusieurs
centaines de Mo et des bibliothèques système GTK/Chromium. Le plugin BnF ne
demande jamais à installer implicitement un navigateur. Si une dépendance système
manque, consulter `docs/CORE-P0-2628-CHROMIUM-MULTIINSTANCE.md`.

## 3. Essai dans l'application

1. Redémarrer `cifereca`, sélectionner **un seul profil Cifereca actif** et
   ouvrir **Configuration → BnF Prêt (lecture expérimentale)**.
2. Cocher le consentement à Chromium, cliquer **Ouvrir le portail BnF** puis
   effectuer soi-même le SSO dans la fenêtre officielle. Aucun mot de passe,
   assertion SAML ou cookie ne doit être communiqué aux tickets.
3. Dans l'écran BnF, cocher l'attestation volontaire du **profil affiché**,
   puis **J'ai terminé la connexion BnF**. Si l'activité n'est pas reconnue,
   utiliser **Diagnostiquer les sélecteurs visibles** et noter **uniquement
   les compteurs agrégés**. Un zéro signifie « non observé », pas « aucun prêt ».
4. Tester **Relire mes prêts**, **Rechercher sur BnF**, **Page suivante (si lien
   visible)**, **Ouvrir sa fiche dans Chromium** et **Examiner les contrôles
   d'emprunt (lecture seule)**. L'activité présente des observations **non
   exhaustives** ; aucune fin de prêt ni synchronisation Core n'est déduite.
5. **Fermer la session**. Changer de profil doit également invalider les
   observations précédentes. Revenir à la fiche Plugins pour désactiver
   `ui.page.bnf-pret` après l'essai si souhaité.

### Résultats et problèmes à signaler

Rapporter seulement : version Core + plugin, état de `plugin diagnose`
**après expurgation** des chemins, étape concernée, code d'erreur fermé,
et les **compteurs DOM agrégés**. Ne joindre **jamais** un ZIP Browser Recorder
privé, une URL de prêt, du HTML de compte, un nom de lecteur, une assertion
SAML, un cookie, un fichier ACSM ou une capture de formulaire connecté.

Cas importants à observer : SSO confirmé mais page d'activité non reconnue,
zéro bloc de prêt malgré un prêt visible à l'écran, résultats vides ou HTML
non qualifié, fiche Book non reconnue, pagination manquante, Chromium impossible
à ouvrir, profil changé pendant une requête. Un refus fermé est préférable
à un résultat inventé.

## 4. Essai technique hors interface (optionnel)

Avec Playwright et son Chromium dans le **même Python** :

```bash
python3 src/plugins/provider-bnf-pret/cifereca_bnf_pret_browser.py
```

Confirmer l'ouverture, finir le SSO manuellement puis utiliser les commandes
`[d]` (comptages DOM), `[l]` (prêts), `[r]` (recherche), `[n]` (page suivante),
`[s]` (suggestions) et `[q]` (fermeture).
Cette CLI locale n'écrit aucun état dans la bibliothèque de Cifereca.

Contrat offline :

```bash
python3 -m unittest discover -s src/plugins/provider-bnf-pret/tests -v
```

Les tests Chromium en CI utilisent uniquement des pages synthétiques :
ils ne valident **ni SSO réel, ni permission d'emprunt, ni ADEPT**.

## Disponible / non disponible

- **Disponible :** navigateur éphémère, connexion manuelle, recherche HTML,
  fiches bibliographiques, contrôles d'emprunt *observés*, compteurs des prêts
  visibles, sélection d'un livre lié à un prêt, pagination bornée et diagnostic DOM.
- **Non disponible :** émission d'emprunt (POST), accès au lien de téléchargement,
  fulfillment ADEPT, publication de droits Core, synchro exhaustive, restitution
  ou réservation.

Un fichier local déjà téléchargé reste conservé indépendamment d'une fin
de prêt ; ce test de lecture n'efface aucun fichier ni provenance.
