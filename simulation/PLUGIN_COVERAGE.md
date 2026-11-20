# Matrice de couverture des plugins — inventaire P2 #770

Le rapport se reconstruit **depuis tous les manifests courants** de
`src/plugins/*/cifereca-plugin.json`. Aucun nombre de packages n'est figé.
La matrice reviewée `plugin_coverage.toml` conserve, séparément, les données
associées et les chemins de scénarios (contrat, erreurs/hors ligne, GTK).
Les chemins sont vérifiés dans le dépôt : une preuve absente, périmée,
extérieure ou un nouveau package non classé fait échouer le contrôle strict.

```bash
python3 simulation/plugin_coverage.py --strict
python3 simulation/plugin_coverage.py --json
python3 -m unittest discover -s simulation/tests -p 'test_plugin_coverage*.py' -v
```

### Colonnes et états

- `roles`, `plugin_ids`, `capabilities`, `ui_routes` : **lu dans le manifest**,
  et non deviné à partir d'une étiquette du tableau.
- `services` : correspondance du vrai identifiant plugin dans le catalogue des
  faux services ; `http_fixtures` : scénario HTTP injectable sans activation du
  fournisseur. Un service déclaré sans activation ne constitue pas un parcours.
- `data`, `fixture_profiles` : données locales/HTTP et profils configurés
  pour les produire ; l'existence d'un profil ne prouve pas que le plugin tourne.
- `unit_test_files` : nombre de fichiers de tests plugin, **pas de PASS CI**.
  `contract_tests`, `failure_tests` et `gtk_scenarios` : références
  explicitement auditées de fichiers existants. **Aucune exécution n'est
  inférée de leur existence**, ni des textes des noms de fichiers.
- `dimensions` : statut indépendant pour `http_fixture`, `dataset`,
  `contract`, `failure_offline`, `gtk_e2e`.
  - `partial` : au moins une pièce statique existe, mais la recette complète
    n'est **pas** attestée.
  - `absent` : aucune pièce recevable.
  - `not_applicable` : par exemple HTTP pour un plugin local qui n'est
    ni un provider HTTP ni un transport.
  - `covered` : réservé à une preuve d'exécution et de périmètre suffisants ;
    **jamais déduit du seul manifeste, des fixtures ou de scripts GTK**.
- `status` global reste `partial` tant qu'il existe seulement des preuves
  statiques. `e2e_gtk_verified=false` est volontaire : les scripts d'E2E
  ne sont pas une attestation de leur exécution.

### Limites et évolution

Ce rapport **n'exécute pas les suites** et ne lit aucune base personnelle :
aucun compte réel, secret, URL privée ni accès réseau. L'attestation d'exécution
GitHub Actions est un résultat distinct, qui n'est pas fabriqué par cet outil.
Certains plugins locaux n'exigent aucun serveur HTTP ; `ux-all` ne signifie
pas que tous les plugins ont une E2E GTK. Les états négatifs documentés peuvent
encore ne pas être exercés sur la GUI.

Un nouveau plugin doit avoir son bloc `[package.nom]` dans la TOML et des
preuves propres si elles existent. Ajouter un chemin de scénario seulement
s'il teste réellement le cas ; une référence périmée ou une preuve d'un autre
package est refusée. Les preuves de scénarios d'intégration communs vivent dans
`simulation/tests/`, les contrats internes dans `src/plugins/<package>/tests/`
et les probes GTK dans `e2e/`. Les tickets #771–#774 suivent les améliorations
du **contenu** des scénarios indépendamment de cet inventaire.

## JDD locaux ajoutés (pilote #772 et #773)

```sh
python3 simulation/plugin_scenarios.py --out /tmp/cifereca-ux-specialized
python3 -m unittest discover -s simulation/tests -p 'test_plugin_*.py'
```

Le générateur crée des données **fictives et déterministes** :
- `youth.json` : intérêt/lecture, 0–6, 17/18, âge inconnu/inféré,
  homonymes d'éditions et preuves ISBN contradictoires ;
- `criticism.json`, `criticism.rss.xml`, `criticism.jsonfeed.json` :
  0/1/N critiques, absence d'association, sources et ISBN ;
- `constellations.json` : titres reliés ou isolés, variations d'humeur,
  séries, accès et deux identités ;
- `optimizer-valid.epub`, `optimizer-corrupt.epub` : première paire
  succès/erreur technique ;
- `optimizer-protected.epub` : marqueur de protection fictif (aucun chiffrement
  ni secret), refus attendu ; `optimizer-compact.epub` : PNG gris 1×1 déjà
  minimal, conservation sous profil générique attendue ;
- `publication-text.pdf` (deux pages texte), `publication-scan.pdf` (une page
  image synthétique sans couche texte), `publication-corrupt.pdf` (tronqué) :
  PDF déterministes sans métadonnées ni DRM, inventoriés par SHA-256.

Le manifeste exige les 16 noms du corpus V6 et leurs SHA-256.
Les fichiers sont produits dans `--out`, **pas versionnés**, et n'installent
aucun abonnement, réseau, identité utilisateur ou compte externe.

**État après l'intégration :** ces fixtures sont générées dans le runtime isolé
par `datasetctl` si `[specialized].enabled = true` (profils `ux-small`,
`ux-all`, `ux-errors`) et signalées par `summary --json`.
Les flux RSS et JSON Feed de critique sont désormais **servis par HTTP local** via
`simulation/editorial/pseudo_editorial_service.py` ; les JSON Jeunesse et
Constellations sont consultables comme fixtures, mais ne sont **pas des API
plugins injectées automatiquement**. Les fichiers ne sont pas injectés automatiquement dans les caches métier.
Pour Publication, des consommateurs de recette isolés utilisent effectivement
ce corpus (voir réception #773 ci-dessous). Ce n'est pas une injection
automatique dans les parcours GUI OpenLibrary, ni une simulation du
provisionnement ADEPT ou du DRM de bout en bout.

## Éditions Milan — HTTP

Le service `milan` sur 127.0.0.1:8101 sert le catalogue et les fiches HTML
synthétiques. Le test **actif** `simulation/tests/test_milan_fixture_edges.py`
vérifie le faux serveur local (pagination, HTML/ISBN, 404, 503, type et
limites), sans importer de plugin ni accéder à un fournisseur extérieur.
L'ancien consommateur `MilanYouthSource` et son test d'intégration ont été
retirés avec les packages Jeunesse (#957) ; ce n'est **ni** une preuve de
conformance du provider **ni** un test GTK.

Voir `simulation/milan/README.md` et #816.

## Baen — couverture de contrat

`simulation/baen/pseudo_baen_service.py` sert maintenant les scénarios
catalogue, recherches, pagination, détails, téléchargements EPUB synthétiques,
429/503 et absence de lien. Le vrai `provider.baen` est exercé avec un
`httpx.MockTransport` dirigé uniquement vers le faux serveur loopback :
`simulation/tests/test_baen_http_service.py`. Le service n'active aucun
plugin Baen dans Cifereca et ne prouve pas un parcours GUI.

## Découverte et enrichissement Jeunesse — fixtures conservées, plugins retirés

Le manifeste des JDD spécialisés est en **version 6** ; les profils
`ux-small`, `ux-all`, `ux-errors` peuvent régénérer les fixtures
`youth-discovery.json` et `youth-enrichment.json`, proposées par le service
éditorial local (`GET /youth/discovery.json` et `GET /youth/enrichment.json`).
Elles représentent des cas synthétiques de recherche de sources, de preuves
ISBN, de doublons, d'âges et d'isolation d'identité ; elles ne constituent
pas une validation de la chaîne applicative.

Les consommateurs historiques `YouthSourceDiscoveryPlugin` et
`YouthDetailEnrichment` ont été retirés (#957). Le test ancien
`simulation/tests/test_youth_workflow_contracts.py` est **absent** de la
suite actuelle : ne pas le citer comme gate de conformance ou tenter de
réactiver ces packages. Les fixtures restent indépendantes du runtime
Cifereca ; ni GUI GTK ni persistance métier ne sont attestées ici.

## Kobo-Adept — profils synthétiques

`adept-profiles.json` fournit **cinq profils purement fictifs** (créé,
incomplet, prêt, sans liseuse et expiré) dans les JDD spécialisés.
`simulation/adept/fake_kobo_adept_cli.py` expose uniquement les commandes
de contrat et de profils, sans compte Adobe, jeton, certificat, ACSM ni DRM.
Le vrai `KoboAdeptRuntime` est testé via un sous-processus Python :
`simulation/tests/test_adept_profile_simulation.py`.

Un profil créé via la fausse CLI est volontairement **incomplet** : aucun
message de réussite de provisioning ne doit être inféré. Les opérations
d'association et de suppression sont des simulations sans mutation.
Il ne s'agit pas d'une validation GTK ni du véritable backend Kobo-Adept.

## Transport HTTP : erreurs et redirections déterministes

Le service `transport_failures` est accessible uniquement sur `127.0.0.1:8102` dans les profils `ux-small`, `ux-all`, `ux-errors`. Le vrai `HttpTransport` est testé contre 429/503, timeout, HTML ou MIME incorrect, taille excessive, réponse vide, boucle de redirection et redirection inter-origines supprimant les en-têtes sensibles. Un EPUB fictif permet aussi un téléchargement positif.

Ces tests ne constituent pas un scénario E2E GTK ni une preuve de couverture du cache. La réception Core #7002 exerce le vrai transport et l'annulation aux frontières de publication ; le contrat transport ne comporte pas d'annulation intra-stream. Voir `simulation/transport/README.md` et `simulation/tests/test_http_negative_contracts.py`.

## OPDS Gallica/Gutenberg — erreurs de protocole

Le serveur OPDS existant accepte `?ux_fault=` pour sept états négatifs
de flux (429/503, XML invalide, mauvais document, zéro notice, échec de
page suivante, redirection bouclée). Des réponses EPUB invalides et
HTML testent `transport.http`. Les tests sont dans
`simulation/tests/test_opds_negative_contracts.py`.

Les scénarios sont **opt-in**, sans impact sur les catalogues des profils
`ux-small`, `ux-all`, `ux-errors` en mode normal ; les preuves de
fonctionnement GTK et les vrais fournisseurs ne sont pas vérifiés.


## Publication et maintenance — #773

`publication-cases.json` lie les payloads générés à des chemins relatifs
**fictifs**, trois identités et des attentes de recette : chaîne
achat A/ADEPT → transfert B → achat indépendant B/ADEPT → transfert C,
un SHA partagé et huit provenances ; keeper, copie en doublon, profil
étranger, Outbox, fichier missing, collision, annulation et reprise.
Aucun chemin de bibliothèque n'est créé par cette fixture et aucun état
SQLite/droit n'est importé. Les attentes ne sont pas des résultats métier.
Les attentes seules ne constituent pas une conversion exécutée. Les consommateurs ci-dessous exercent effectivement les moteurs et les contrats Core ; les suites métier #3171/#3275/#3176 restent propriétaires de leurs invariants.


### Réception technique #773 — preuves exécutées, distinctes de l'inventaire statique

État des livraisons fusionnées au 20/11/2026. Les tests métier sont réutilisés,
sans dupliquer les moteurs. Les liens renvoient aux PR et aux runs de leur HEAD
exact ; le rapport statique ne transforme toujours pas ces références en PASS.

| Domaine | Preuve reçue | Limite / responsabilité |
|---|---|---|
| Publication A/B/C | [#6974](https://github.com/pzia/cifereca/pull/6974), `tests/test_core_p0_773_publication_fixture_reception.py` : vraie SQLite, 3 placements, 8 provenances, SHA partagé, achat indépendant et replay | Core #3171/#3275 ; aucune mutation réelle |
| Maintenance | [#6980](https://github.com/pzia/cifereca/pull/6980), `tests/test_core_p0_773_maintenance_fixture_reception.py` : keeper, chemins interdits, collision, missing et garde physique ; audits couvertures/langues : contrats propres `maintenance-library-audit/tests/test_contract_039.py` et suites Core #3995 | Plugins/Core #3995 : audits documentaires à qualifier séparément ; pas de nettoyage effectif |
| Optimizer | [#6982](https://github.com/pzia/cifereca/pull/6982), [#6983](https://github.com/pzia/cifereca/pull/6983), [#6995](https://github.com/pzia/cifereca/pull/6995) : vrais moteurs EPUB/PDF et broker/journal durable, refus corrompu/protection fictive, image minimale conservée, géométrie/texte et SHA | Pas de déchiffrement ; profils logiciels ≠ recette sur liseuse |
| GTK | [#6991](https://github.com/pzia/cifereca/pull/6991), [run 44681285124](https://github.com/pzia/cifereca/actions/runs/44681285124) : optimizer-handoff-1366/1600, vrais clics XTEST, annulation avant worker, erreur et succès EPUB, OriginLink exact | Pas de moteur PDF natif dans le runtime E2E ; PDF reçu hors GTK par #6983 |
| ADEPT | [#6995](https://github.com/pzia/cifereca/pull/6995), [run 44681814140](https://github.com/pzia/cifereca/actions/runs/44681814140) : harnais simulation exécuté, CLI synthétique sans secrets, absent/incomplet/prêt/sans liseuse/expiré | Runtime Kobo-Adept : ni vrai provisioning ni clé/licence réelle |
| Transport et reprise | [#7002](https://github.com/pzia/cifereca/pull/7002), [run 44683753015](https://github.com/pzia/cifereca/actions/runs/44683753015), [#7007](https://github.com/pzia/cifereca/pull/7007), [run 44684490811](https://github.com/pzia/cifereca/actions/runs/44684490811) : transport loopback, 429/timeout, annulation après GET/reçu/promotion, réouverture SQLite, EPUB/PDF et aucun GET après reçu durable | Transport Plugins : pas d'annulation intra-stream ni mesure réseau fournisseur |
| Maintenance et Diagnostics GTK | [run 44683153718](https://github.com/pzia/cifereca/actions/runs/44683153718), HEAD `cb8ffff285dce7a28df5f61c95c90cb2a974b7bd` : verdicts PASS maintenance-tools-920/1024/1280/1366/1600/1728, diagnostics-states/structured/volume/navigation/navigation-slow-1366, diagnostics-1366/raw-export-1366/large-1600 | Fixtures GUI propres, pas le corpus Publication injecté ; propriétaire Diagnostics/UX, pas de preuve terrain de latence |

La nouvelle recette `test_p0_773_generated_device_profiles.py` vérifie le
même corpus EPUB + PDF texte/scan avec Xteink X3/X4, Clara BW et générique,
sans matériel, et exige le profil exact, l'intégrité des sources et les
invariants de document. Sa réception dépend de sa propre CI.
