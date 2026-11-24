# P1 #5540 — inventaire des comptes fournisseurs dans Paramètres

**Périmètre :** état constaté dans les `ProviderDescriptor` et les éditeurs
déclaratifs de `main`, le 14 novembre 2026. Cet inventaire ne modifie ni
authentification, ni droits, ni persistance. Il distingue **déclaration
du plugin** et **preuve de comportement à l'exécution**.

## Matrice déclarative

| Source | Type / accès | Champs credential | Comptes et emprunteurs déclarés | Options présentées par le contrat | Prêts / plafond personnel |
| --- | --- | --- | --- | --- | --- |
| **BnF — Catalogue général** (`bnf-catalogue`) | Catalogue public SRU, anonyme | Aucun | Aucune identité emprunteur déclarée ; `connect()` renvoie `[]` | Aucune | Aucun prêt ni plafond |
| **Gallica** (`gallica`) | Catalogue public, anonyme | Aucun | `connect()` ne renvoie **aucun** `Account`, même pour une ancienne connexion explicite ; ses options enregistrées restent inchangées | `opds_url` (URL HTTP/HTTPS) | Pas de capacité de prêt |
| **Project Gutenberg** (`gutenberg`) | Catalogue public OPDS, anonyme | Aucun | `connect()` renvoie `[]` pour le singleton comme pour les connexions historiques ; aucune identité emprunteur | Contact du User-Agent (préférence locale) | Aucun prêt ni plafond |
| **Ebooks libres et gratuits** (`ebooksgratuits`) | Catalogue public OPDS/HTML, anonyme | Aucun | `connect()` renvoie `[]` même après reconnexion d’une ancienne source enregistrée | Options catalogue locales | Aucun prêt ni plafond |
| **Kobo** (`kobo`) | Bibliothèque privée par **Kobo-Vault**, token | `api_token` secret | `multiple_credentials=True` mais `max_accounts_per_identity=1` ; pas de lecteur lié déclaré | `base_url` (URL HTTP/HTTPS), `display_profile` (choix Android/Desktop, défaut Android) | Pas de plafond de prêt |
| **Numilog** (`numilog`) | Mes achats / bibliothèque personnelle | `email`, `password` secret | Plusieurs credentials déclarés ; pas de lecteur lié | Aucune option de compte déclarée dans ce descriptor | Achat, pas de quota de prêt |
| **BnF Prêt** (`bnf-pret`) | Compte lecteur / prêts numériques | `username`, `password` secret | Plusieurs credentials possibles ; un emprunteur par connexion | `manual_loan_limit` entier [0, 99], défaut 4 | Limite **personnelle indicative** ; inventaire décrit comme partiel |
| **Bibliothèques de Paris** (`paris`) | Compte lecteur, catalogue et prêts | `username`, `password` secret | Plusieurs credentials, compte principal et lecteurs liés | `base_url` (URL HTTP/HTTPS) ; plafonds principaux `manual_loan_limit_primary_digital` et `manual_loan_limit_primary_physical` (entiers [0, 99], défaut 4) ; `manual_loan_limits_linked` par lecteur et branche `digital` / `physical` | Digital DILICOM **séparé** du physique SYRACUSE ; les deux plafonds sont indicatifs |
| **Médiathèques de La Rochelle / BiblioAccess** (`biblioaccess`) | Compte lecteur / SSO CAS | `username`, `password` secret | Plusieurs credentials déclarés ; pas d'option par lecteur lié | `authenticated_sync` choix `off/on`, défaut **off** ; `manual_loan_limit` entier [0, 99], défaut 4 | Préférence indicative, jamais quota fournisseur ; SSO ponctuel interactif distinct de l'opt-in automatique |

**Sources canoniques :** `src/plugins/provider-*/cifereca_*.py`
(et notamment `provider-biblioaccess/cifereca_biblioaccess.py`,
`provider-bnf-pret/cifereca_bnf_pret.py`,
`provider-paris/cifereca_paris.py`,
`provider-kobo/cifereca_kobo.py`,
`provider-numilog/cifereca_numilog.py`,
`provider-gallica/cifereca_gallica.py`,
`provider-bnf-catalogue/cifereca_bnf_catalogue.py`).
Ces caractéristiques sont celles des descriptors ; une capacité déclarée
n'atteste pas la validité d'un abonnement ou d'une session distante.

## Contrat déjà présent sur HEAD

- `src/cifereca/providers/base.py` : `CredentialField` (secret/obligatoire),
  `AccountOptionField` avec `text`, `unsigned_integer`, `choice`,
  `url` et `boolean`, bornes, choix, aide, repli de migration et marqueur
  de plafond personnel ; `BorrowerScopedOptionField` pour
  `emprunteur unique_id + branche`. Les valeurs numériques sauvegardées
  demeurent des chaînes décimales bornées. Les options `boolean` exigent
  un opt-in `off` initial ; les URLs excluent notamment le userinfo.
- **Paris :** les deux plafonds primaires ont désormais une portée déclarative explicite : `advisory_loan_medium="digital"` (DILICOM) et `advisory_loan_medium="physical"` (SYRACUSE). Le champ physique ne participe jamais au calcul du reste numérique ; ces métadonnées ne sont ni un quota serveur ni un droit de prêt.
- `src/cifereca/gui/settings_account_editor.py` : éditeur déclaratif
  `Adw.PasswordEntryRow` pour credential secret, `Adw.ComboRow`
  pour un choix, contrôle booléen, entrée et éditeur des options par
  emprunteur. Les clés de champ sont des données du descriptor, pas
  des branches `if provider_id == …` dans l'éditeur. Un ancien
  paramètre d'URL **global** n'est repris dans l'éditeur que si la
  configuration ne contient **qu'une seule connexion du fournisseur**
  et si son ID correspond exactement au compte édité. Avec plusieurs
  comptes (y compris désactivés ou répartis sur plusieurs profils), cette
  URL sans portée reste historique et n'est copiée dans **aucun** compte :
  chacun affiche sa valeur propre ou le défaut du descriptor.
- `src/cifereca/gui/settings_accounts_section.py` et les contrôleurs
  `settings_*` : section de comptes liée au profil ; revue de la durée
  de vie des actions et de la cohérence de leur rendu encore nécessaire.
- `src/cifereca/core/digital_loan_availability.py` et
  `docs/COREUI-P1-6002-DIGITAL-LOAN-AVAILABILITY.md` : la préférence
  personnelle **ne vaut jamais** disponibilité ou autorisation serveur.
  Un prêt déjà observé ne prouve pas que l'inventaire est complet.

## Écarts précis à traiter dans #5540

1. **Catalogue anonyme (tranche #5540 du 21/11) :** `bnf-catalogue` et
   `gallica` renvoient désormais `[]` lors de `connect()`, y compris
   pour une connexion Gallica historiquement configurée. Aucune identité
   emprunteur fictive n'est publiée au runtime. Les anciennes lignes
   `providers.accounts[]`, leurs préférences `opds_url` et leur profil
   local restent **stockés sans migration ni suppression implicite**.
   Leurs éventuelles migrations de stockage et les droits fournisseur
   ne sont pas couverts par cette tranche.

2. **Multi-comptes :** expliciter la différence entre
   `multiple_credentials` et `max_accounts_per_identity`
   (Kobo déclare les deux). La liste et l'éditeur doivent appliquer le
   maximum du descriptor, avec refus fermé en contexte ambigu.
3. **Options de portail :** `base_url` chez Kobo/Paris et `opds_url`
   chez Gallica sont actuellement déclarées comme `text`, alors que le
   contrat propose désormais `url` avec validation explicite de schémas
   et refus des URL portant des credentials. Requalifier option par option
   après tests de migration et compatibilité des services locaux ; ne
   pas changer leur protocole sans consentement.
4. **Éligibilité et plafonds :** conserver trois faits séparés :
   plafond renseigné par l'utilisateur, prêts actifs positivement observés
   et éventuel quota attesté par le fournisseur (avec fraîcheur).
   Ni un SSO réussi ni une page MyLoans simplement chargée ne suffisent
   à calculer un `restant` fiable.
5. **Recette restante :** un provider synthétique sans règle de marque doit
   créer/éditer/supprimer deux comptes sous deux profils, préserver les
   sections principal/lié/branche, rejeter callback périmé, secrets, borne
   invalide et faux opt-in ; le rendu GTK 960/1366/1600 doit être vérifié.
   Ce document **n'est pas** cette preuve d'acceptation.

**Frontières :** aucune refonte `services.py` (#5975 P0), aucune
modification des protocoles provider, de Sync, d'emprunt, d'achat ou du
chantier USB. Les changements de types de champs et les migrations
doivent être livrés en PR distinctes avec CI/GTK sur HEAD exact.

## Réception incrémentale — 24 novembre 2026

- #7936 : invalidation du diagnostic ponctuel après édition, y compris ABA.
- #7939 : choix DisplayProfile Android/Desktop et correction explicite des anciennes valeurs hors domaine ; aucune conversion silencieuse.
- #7940 : options URL Kobo, Paris et Gallica déclarées avec HTTP/HTTPS ; validation syntaxique, sans changement de transport ni réécriture automatique. Les valeurs historiques hors contrat nécessitent une correction explicite avant sauvegarde.
- #7941 : observation locale du lifecycle en cours de réception ; ne pas confondre connexion observée, diagnostic ponctuel, Sync et autorisation métier.
- La recette native multi-comptes existe dans `e2e/settings_account_lifecycle_acceptance.py` ; les verdicts effectifs et le HEAD exact restent à consigner avant clôture.

Les écarts ci-dessus datés du 14 novembre constituent un historique, non une liste de travaux à refaire. Aucune qualification fournisseur réelle ni quota officiel nouveau n'est revendiqué.
