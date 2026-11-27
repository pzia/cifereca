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

## Critère de découplage P0 #5975 — priorité du 16/11/2026

**Livrer d'abord la séparation Core/provider effective.** La sortie P0 n'exige pas zéro mention fournisseur ni zéro AST brut : elle exige **zéro branche métier décisionnelle par fournisseur dans Core/CoreUI, zéro writer legacy fournisseur encore autoritatif et des contrats Plugins/capabilities source-neutres consommés par les vrais appelants**. Mesurer les décisions métier retirées, les chemins actifs et les signaux AST distinctement ; les migrations historiques, diagnostics et gardes justifiés peuvent subsister.

**Intégrité minimale :** opération qualifiée sous la vraie identité/source/credential/compte, consentement et offre actuels pour toute livraison, pas de droit inféré de C/A ou d'un `rights` legacy, pas de double mutation fournisseur, pas de corruption/effacement de copie locale unique, pas de fuite de secrets. Les races ou réconciliations historiques rares non bloquantes peuvent être documentées et différées, mais jamais une mutation possible sous le mauvais compte. Une liaison `OriginLink/Placement → EPUB` peut être récupérée plus tard si **le téléchargement actuel reste réellement admissible** ; l'absence de lien n'est pas une preuve d'absence du fichier ni de droit au re-GET.

L'ABI cible [ARCHITECTURE.md](ARCHITECTURE.md) ne change pas. Le cadrage prioritaire et les exceptions de clôture sont décrits dans [#5975](https://github.com/pzia/cifereca/issues/5975), [AGENTS.md](../AGENTS.md#5975--recentrage-opposable-du-16112026--découplage-dabord) et [la politique de livraison](AGENT-DELIVERY-POLICY.md#application-particulière--p0-découplage-5975-arbitrage-du-16112026). L'audit #5376 peut rester ouvert sans bloquer la sortie du découplage.

## Réception P0 Notices C + miniatures #6366 — 16/11/2026

La preuve minimale [#6366](https://github.com/pzia/cifereca/issues/6366) est un chemin **fournisseur réel → observation C persistante et indexée → Search C-first progressif → même get_cover(DocumentRef, variant) avec N1/N2/N3 dans les six vues liste/détail**. Un rapprochement multi-source exige une même édition fortement attestée ; à défaut, deux C distinctes et placeholder si nécessaire. La C survit au TTL source/HTTP, au redémarrage et à une recherche offline stale.

Le P0 ne bloque plus sur toutes les combinaisons multi-profils/caches/ABA/TTL, le GET strictement unique en tout scénario, les raffinements UX/invalidations ou un scheduler général #4982. **Les invariants restent** : pas de rapprochement fictif, mauvaise couverture, faux droit, contamination entre comptes, secret persisté, mutation fournisseur sans admission, perte du seul EPUB ou corruption. Ces exceptions P0 bornent la preuve de clôture, pas l'architecture A/B/C ni le contenu à livrer dans les P1 #2385/#4552/#3724/#6168/#3104/#4979.


### Réception historique Notices — 26/11/2026

Les maîtres [#7808](https://github.com/pzia/cifereca/issues/7808) et [#7810](https://github.com/pzia/cifereca/issues/7810) ont été absorbés administrativement dans [#7814](https://github.com/pzia/cifereca/issues/7814), clos après réception des 14 lots, sans double comptage ni réouverture. Ce jalon ne modifie pas les invariants ci-dessus ni les dépendances consommées de #7812.

Preuves datées : [#1023](https://github.com/pzia/cifereca/issues/1023#issuecomment-7184173978) et [#6168](https://github.com/pzia/cifereca/issues/6168#issuecomment-7183985978), puis [#4980](https://github.com/pzia/cifereca/issues/4980#issuecomment-7191469001) et [#3104](https://github.com/pzia/cifereca/issues/3104#issuecomment-7191472360). La [PR #8165](https://github.com/pzia/cifereca/pull/8165), intégrée sur `f5645ad155cb6b57edc714795abe17a180807b91`, a reçu une matrice GTK synthétique (54 scénarios, 224 captures, 12 surfaces ABA par configuration). Le reçu automatique n'atteste ni une inspection visuelle humaine ni une qualification terrain : arbitrages B/B+C du 26/11, revue humaine #8164, terrain #6875/#8154, qualification positive de miniatures protégées #8153, sans PASS induit. Le maintien de N1 admissible, N2 décision scoppée/versionnée, N3 octets validés et N4 texture effectivement appliquée reste distinct du verdict descriptif C. Pour les preuves actuelles, réexaminer le HEAD installé ; aucun nouveau chantier n'est déclenché par ce rappel historique.

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
