# Autorité documentaire Core / plugins — index normatif

**Statut : actif — 13 novembre 2026 · ticket [#5519](https://github.com/pzia/cifereca/issues/5519).**

Cet index est **le point d'entrée de classement**, pas une nouvelle ABI. La frontière publique Python se nomme `cifereca.plugin_api`. L'état livré se vérifie dans `src/cifereca/plugin_api/`, les signatures Python et les tests du **HEAD installé**. Une architecture cible ne doit jamais être présentée comme un comportement déjà déployé.

| Niveau | Référence | Ce qu'elle fait foi |
| --- | --- | --- |
| **Normatif — cible métier** | [ARCHITECTURE.md](ARCHITECTURE.md) / [#3171](https://github.com/pzia/cifereca/issues/3171) | Origine exacte → offre actuelle → Artifact/SHA/Placement ; Sync unique, droits non inférés d'une notice |
| **Normatif — guide fournisseur commun** | [PROVIDER-DEVELOPMENT.md](PROVIDER-DEVELOPMENT.md) | Frontières Core / provider, catalogue C / inventaire A / livraison B, choix du plugin, tests |
| **Normatif — frontières techniques** | [PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md) | Responsabilités Core/Plugins, HTTP(S), diagnostics, règles d'import |
| **Contrat installé — exécutable** | [`src/cifereca/plugin_api/`](../src/cifereca/plugin_api/) et [`providers/base.py`](../src/cifereca/providers/base.py) | Signatures, Protocols, dataclasses, catégories réellement disponibles ; **priment sur les exemples** |
| **Contrat de livraison du kit** | [Guide copié dans Browser Recorder](../src/plugins/browser-recorder/PROVIDER-DEVELOPMENT.md), [générateur](../src/plugins/browser-recorder/browser_recorder_devkit.py) | La copie du guide doit rester identique octet pour octet ; les contrats embarqués décrivent le Core réellement installé |
| **Contrat spécialisé — sécurité/diagnostics** | [LOGGING-CONTRACT.md](LOGGING-CONTRACT.md), [DIAGNOSTICS_CANONICAL_JOURNAL.md](DIAGNOSTICS_CANONICAL_JOURNAL.md) | Journaux bruts et exports volontaires ; ne remplacent pas les gardes de credentials, de droits ni de publication |
| **Preuves fournisseur — non transversales** | [RIGHTS-OBSERVATION-CONFORMANCE.md](../src/plugins/RIGHTS-OBSERVATION-CONFORMANCE.md), [QUALIFICATION BnF](../src/plugins/provider-bnf-pret/QUALIFICATION.md) | Couvertures de lecture, routes et limites **observées** pour le fournisseur, jamais politiques globales |
| **Historique / preuve datée** | `CORE-P0-3196-LEGACY-*`, `KOBO-LOCAL-RECONCILIATION.md`, `PLUGIN-P1-3252-RIGHTS-OBSERVATION.md`, `HANDOFF-*.md` | Contexte et audits passés uniquement ; **aucune injonction normative active** sans révalidation contre les contrats installés |

## Invariants d'interprétation

- **C — catalogue** : notice descriptive, publique ou privée selon son origine. Elle ne démontre **ni droit, ni prêt, ni achat, ni lien de livraison**, et n'autorise pas à partager un résultat d'une session étrangère.
- **A — inventaire personnel** : faits positivement observés pour `provider + identity + credential + account + branch + edition + loan occurrence` et couverture de lecture explicite. Une réponse partielle, en particulier **BnF**, n'atteste jamais une absence exhaustive ; elle ne révoque ni prêt, ni fichier.
- **B — acquisition** : une offre de livraison actuelle et positive du compte exact, plus consentement, révocation/epoch, intégrité et contrôles d'opération. La **Sync ne crée pas de prêt** ; un GET de confirmation peut être mutateur et ne doit pas être rejoué après résultat ambigu.
- **Stockage** : `OriginLink → Artifact/SHA final → Placement` vérifiés **dans le profil**. Le même SHA ne fusionne jamais droits, prêts, origines ou placements inter-profils. Les anciens `rights`/`Entitlement`/`backup_items` sont une **compatibilité technique**, pas une deuxième autorité.
- **Recherche inter-identités** : la projection publique doit être attestée par une notice issue de la **réponse courante**, sans accès aux credentials, URL privée ni acquisition. Le contrat `PublicSearchNotice`/`ProviderSearchResponse` peut être installé sans que les plugins réels l'émettent : vérifier cette disponibilité séparément (coordination [#5802](https://github.com/pzia/cifereca/issues/5802), [#5781](https://github.com/pzia/cifereca/issues/5781)).
- **HTTP(S)** : transport partagé et brokers publics ; la logique d'authentification et les routes métier restent chez le fournisseur. Aucun import de modules Core/SQLite privés par le plugin.

## Hiérarchie et documents historiques

1. **Contrat en production :** code `plugin_api`, package/provider descriptors, version installée et tests. Si l'ABI manque, ne pas la supposer en lisant une cible.
2. **Politique métier cible :** `ARCHITECTURE.md`, puis `PROVIDER-DEVELOPMENT.md` pour la mise en œuvre commune ; `PLUGIN-BOUNDARIES.md` pour les frontières.
3. **Protocoles fournisseur :** README/tests/qualifications propres au plugin. Leurs preuves sont **locales au provider** et ne changent jamais la politique transverse.
4. **Notes legacy, handoffs, anciens README de release :** archives de décision et de preuves, conservées dans Git pour traçabilité. Leur date et leur titre ne garantissent ni l'ABI courante, ni l'achèvement d'une migration.

Les documents historiques ne sont **ni réécrits en masse ni supprimés** par cet index. Exemples explicitement laissés en place : `docs/KOBO-LOCAL-RECONCILIATION.md`, `docs/PLUGIN-P1-3252-RIGHTS-OBSERVATION.md`, les notes `docs/CORE-P0-3196-LEGACY-*`, les mémos `src/plugins/RIGHTS-*.md` et les fichiers racine `HANDOFF-*.md`. Les liens individuels et leurs dates servent à retrouver la preuve, **pas à prescrire le comportement**.

## Mesures de découplage — ne pas confondre les catégories

Le [ticket #5519](https://github.com/pzia/cifereca/issues/5519) rapporte pour le **11 novembre 2026** une photographie de **2 670 LOC spécialisées historiques** et **302 lignes génériques uniques portant un signal AST** (après reclassement). Dans les 2 670 LOC : BnF 997, Kobo 653, Numilog 531, Paris 489. L'ancien compteur lexical générique de 1 958 lignes comprend des noms dans les commentaires/chaînes : **il ne représente pas 1 958 LOC métier**. Les catégories ont des recouvrements ; ne pas additionner sans méthode.

Pour **recalculer** sur le `main` actuel, et inscrire le HEAD exact dans tout rapport :

```sh
git rev-parse HEAD
python3 scripts/ci_provider_core_coupling_report.py --json
python3 -m scripts.ci_provider_ast_function_worklist --json
```

Ne pas citer ces chiffres historiques comme solde courant, et ne pas publier un nombre sans sa date, son périmètre, la commande et le commit. Les tickets [#5048](https://github.com/pzia/cifereca/issues/5048), [#5518](https://github.com/pzia/cifereca/issues/5518), [#5376](https://github.com/pzia/cifereca/issues/5376) pilotent les migrations effectives.

**Gate borné** : `python3 scripts/ci_provider_document_authority.py` valide les renvois normatifs prioritaires et la copie identique du guide du kit ; il ne catégorise pas automatiquement toutes les 343 notes historiques et ne cherche pas naïvement le mot « rights ».
