# P1 #5540 — matrice des comptes et préférences fournisseurs

**Inventaire de code au 14/11/2026**, sans appel réseau, authentification ni
hypothèse d'activation à l'installation. Références : les déclarations
`DESCRIPTOR` des plugins listés ci-dessous, `src/cifereca/providers/base.py`,
`src/cifereca/gui/settings_account_editor.py` et
`src/cifereca/gui/settings_draft.py`.

Un `ProviderDescriptor(state="ready")` ne prouve ni qu'un bundle est
**activé**, ni qu'une session est **connectée**, ni que des prêts ou quotas
sont **observés**. Une capacité `search` ne prouve pas un `catalog.refresh`
ou un inventaire exhaustif.

## Sources avec compte et identifiants

| Provider, source | Authentification / cardinalité déclarée | Champs de connexion | Options visibles réellement déclarées | Identité, emprunteur et réserves |
|---|---|---|---|---|
| **Kobo** — `provider-kobo/cifereca_kobo.py` | Token d'API Kobo-Vault ; `multiple_credentials=True`, **au plus un compte par identité** | `api_token` secret | `base_url` **texte** ; `display_profile` **texte**, défaut `Android`, ancien fallback `kobo_backup.display_profile` | API Kobo-Vault distincte du catalogue public Kobo. Ne pas transformer l'URL du Vault ou un token en droit bibliographique. Les valeurs historiques de DisplayProfile n'ont pas encore été qualifiées pour une liste de choix fermée. |
| **Numilog privé** — `provider-numilog/cifereca_numilog.py` | Identifiants Numilog ; `multiple_credentials=True` | `email`, `password` secret | **Aucune** `account_options` déclarée | Le catalogue commercial public et `Mes achats` restent deux sources d'observation différentes ; aucune préférence de quota manuel uniforme déclarée ici. |
| **Paris** — `provider-paris/cifereca_paris.py` | Identifiants ; `multiple_credentials=True`, `linked_accounts=True` | `username`, `password` secret | `base_url` **texte**, défaut `https://bibliotheques.paris.fr` avec fallback legacy ; `manual_loan_limit_primary_digital` et `manual_loan_limit_primary_physical` **entiers non signés 0–99**, défaut `4` chacun | **Principal** : numérique DILICOM et physique SYRACUSE **séparés**. **Comptes liés** : `BorrowerScopedOptionField(manual_loan_limits_linked)`, clé du lecteur exact et branche `digital/physical`, défaut `4`, bornes 0–99. Aucun plafond n'est un quota fournisseur ni transférable à un autre emprunteur. |
| **BnF-Prêt** — `provider-bnf-pret/cifereca_bnf_pret.py` | Identifiants ; `multiple_credentials=True` ; un emprunteur exact par session | `username`, `password` secret | `manual_loan_limit` **entier 0–99**, défaut `4` | Plafond personnel indicatif seulement. Les prêts synchronisés sont déclarés **partiels** ; aucune soustraction automatique `4 − prêts connus` ne prouve des places libres. |
| **La Rochelle / BiblioAccess privé** — `provider-biblioaccess/cifereca_biblioaccess.py` | `multiple_credentials=True`, `interactive_authentication=True` ; SSO d'arrière-plan seulement avec opt-in exact | `username`, `password` secrets, requis | `authenticated_sync` **choice** `off/on`, défaut `off` ; `manual_loan_limit` **entier 0–99**, défaut `4` | Le SSO CAS est autorisé seulement si `authenticated_sync == "on"` sur le compte exact. `off` ne bloque pas une vérification ponctuelle explicitement demandée ; le plafond local ne modifie pas ACSM, droits ou admission. Anciennes options AJAX/diagnostic opaques conservées mais cachées dans l'éditeur courant. |

## Catalogues publics et sources sans compte lecteur

| Source | Identifiants utilisateur | Déclarations d'options | Contrat à ne pas déduire |
|---|---|---|---|
| **Gallica** — `provider-gallica/cifereca_gallica.py` | Aucun `CredentialField` | `opds_url` encore **texte**, défaut `https://gallica.bnf.fr/opds` | Option d'URL de catalogue ≠ compte connecté ni bibliothèque privée ; éviter une pseudo-identité/connexion pour présenter un fournisseur anonyme. |
| **Project Gutenberg** — `provider-gutenberg/cifereca_gutenberg.py` | Aucun | Aucune option de compte déclarée | Recherche OPDS publique, sans inventaire de prêts ni identité de compte. |
| **Ebooks libres et gratuits** — `ebooksgratuits/cifereca_ebooksgratuits.py` | Aucun | Aucune option de compte déclarée | OPDS public et fichiers EPUB, non bibliothèque privée d'un lecteur Cifereca. |
| **Baen Free Library** — `baen/cifereca_baen.py` | Aucun pour la consultation publique | Aucune option de compte déclarée | Plugin désactivé par défaut ; recherche distante incomplètement qualifiée (`partial_results`), pas `My Books` ni achat ou téléchargement accordé. |
| **La Rochelle catalogue public** — second provider du bundle `provider-biblioaccess/cifereca-plugin.json` | Aucun ; instance `singleton_public` | Aucune option de compte de la session privée | Catalogue HTML/JSON public distinct du SSO et de la synchronisation MyLoans. |
| **Numilog catalogue public** — bundle `provider-numilog` | Aucun lecteur privé pour la recherche publique | Pas de quota personnel | Fournisseur catalogue public séparé, désactivé par défaut : son existence n'implique ni activation, ni couverture HTTP complète, ni droits `Mes achats`. |

Les catalogues publics peuvent être déclarés comme fournisseurs disponibles
dans Recherche sans générer de `providers.accounts[]` fictif. La présence
d'un champ `AccountOptionField` (Gallica) n'autorise pas à demander un secret.

## Contrat partagé effectivement disponible

Le Core expose les types déclaratifs `text`, `unsigned_integer`,
`choice` (menu de tokens stricts), `url` (HTTPS par défaut, HTTP
explicitement autorisé) et `boolean` (off par défaut), dans
`AccountOptionField`. Les types nouvellement disponibles ne migrent
**aucun champ historique par magie**. L'éditeur et le draft génériques
préservent les options inconnues et requièrent une validation stricte :
un token non déclaré ou un formulaire périmé ne choisit pas une autre
valeur ni un autre consentement.

**Preuves déjà intégrées :** #6001 (choice), #6016 (url), #6019
(choix SSO La Rochelle), #6021 (boolean). **Proposition distincte en
CI :** #6010 porte `advisory_loan_medium` et la projection de
disponibilité inconnue en Accueil ; ne pas compter ses capacités comme
déjà fusionnées.

## Écarts d'acceptation encore ouverts

1. **Migration fournisseur contrôlée** des URL Gallica/Kobo vers `url`,
   seulement après inventaire de la forme des valeurs utilisateur
   existantes ; ne pas rejeter silencieusement des chemins ou query
   historiquement autorisés. Idem pour `display_profile` Kobo vers
   `choice` : prouver toutes les valeurs effectivement acceptées avant
   de fermer le domaine.
2. **État de connexion observé**, expiration/révocation, dates et
   statuts de synchronisation distincts du seul `state="ready"` du
   descripteur. Les valeurs configurées ne deviennent pas des observations.
3. **UI multi-comptes / lecteurs liés** : distinguer explicitement le
   couple `identity/provider/credential`, l'emprunteur primaire ou lié
   exact et sa branche, sans exposer les identifiants ou URL secrets dans
   diagnostics/tooltips, ni créer de faux comptes sur catalogue public.
4. **Recette GTK/installation** sur 0, 1 et plusieurs comptes, profil
   A→B→A, comptes Paris primaire + liés, quotas BnF partiels,
   La Rochelle `on/off`, options legacy, et écran catalogue public
   sans connexion ; aucune opération d'emprunt/acquisition en affichage.

Cette matrice est **descriptive**, pas une nouvelle ABI, une migration de
config, un appel réseau, un inventaire complet ou une preuve d'autorisation.
Le ticket parent #5540 reste ouvert jusqu'à recette des écarts.
