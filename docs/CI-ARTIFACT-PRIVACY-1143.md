# CI/CD P0 #1143 — audit de publication des artefacts (19 octobre 2026)

> **Interprétation produit actualisée (26/10/2026) :** les seules données à protéger au titre de la confidentialité sont les **credentials** : mots de passe, jetons bearer/refresh, cookies de session authentifiants, clés privées/secrets API et liens signés donnant accès. La présence de noms, comptes, chemins, titres, identifiants, textes d'erreur, URL ordinaires, traces ou captures **sans credential** ne motive ni un nouveau ticket de confidentialité, ni des purges, expurgations ou contrôles supplémentaires. Les règles historiques ci-dessous documentent les **artefacts et limites de publication réellement implémentés** ; elles ne constituent pas une demande d'élargissement des travaux à la confidentialité des autres champs. Les contrôles existants continuent de s'appliquer jusqu'à modification explicite du pipeline par CI/CD ; ne jamais publier automatiquement de credentials.


## Périmètre vérifié

**Dépôt public** : les journaux des workflows, step summaries et artefacts Actions peuvent être lus hors équipe. La revue couvre **tous** les fichiers de `.github/workflows/*.yml` sur `main`, et les producteurs historiques cités dans #1143. Les workflows sans `upload-artifact` ne produisent pas d'archives Actions, mais leurs logs restent publics.

La politique est **refus par défaut** : aucune capture PNG, `screen.json`, trace JSONL, sortie complète de processus, fichier XDG/HOME, cookie, lien ACSM signé ni configuration de compte réel ne peut être publié automatiquement. L'absence de jeton évident ne suffit jamais à déclarer sûre une capture d'écran.

## Producteurs Actions actuellement présents

| Workflow | Publication exacte | Rétention | Condition et contrôle |
| --- | --- | --- | --- |
| `e2e-smoke.yml` | `$RUNNER_TEMP/cifereca-e2e-public/summary.json` | **2 jours** | `ci-export-e2e-summary.py` génère un **nouveau** verdict fermé `schema/scenario/status/returncode`, métriques bornées et indices de ligne AST vérifiés. **Jamais** le dossier `e2e-artifacts/`. |
| `gtk-smoke.yml` | `$RUNNER_TEMP/gtk-smoke-public/result.json` | **2 jours** | Constantes de scénario et booléens d'existence uniquement ; pas de PNG ni de rapport GTK. |
| `gtk-e2e-on-approval.yml` | `/tmp/gtk4-debian13-rootless-offline.tar.gz`, `/tmp/SHA256SUMS` | **2 jours** | `workflow_dispatch` + confirmation humaine ; tar Debian `.deb` construit depuis une liste positive, aucun checkout, log ou profil. Lié à la campagne #1011 ; supprimer le relais après clôture de cette campagne avec accord du propriétaire. |
| `package-plugins.yml` | Deux ZIP nommés et liés au SHA de commit | **2 jours** | Packaging Core + plugins connu, validation ZIP/SHA. Pas de wildcard `dist/**`. |
| `plugins-only-preview.yml` | ZIP de preview nommé par run/attempt et son SHA256 | **2 jours** | Preview dédiée, pas de GitHub Release ni d'archive du répertoire de travail. |

`release.yml` et `plugins-only-release.yml` utilisent **GitHub Releases** (pas `upload-artifact`) : artefacts de distribution ciblés, validations de cohérence et signature applicables, autorisation de publication distincte indispensable. Une simple PR ou un push de code n'autorise pas une nouvelle release.

**Step summaries** : seul `e2e-smoke.yml` écrit explicitement `GITHUB_STEP_SUMMARY`. Il recopie exclusivement le résumé fermé déjà exporté dans `$RUNNER_TEMP/cifereca-e2e-public/summary.json`, jamais le `summary.json` brut des scénarios. Aucun chemin, URL, compte, phrase de diagnostic ou capture n'est extrait du rapport par l'exporteur.

**Sorties locales de test** : `e2e/run.py` utilise des fixtures versionnées synthétiques, HOME/XDG temporaires séparés ; sorties stdout/stderr du sous-processus capturées en mémoire, **expurgées avant** d'écrire les logs, avec troncature à 1 million de caractères. `screen.json`, `trace.jsonl`, captures PNG et les résumés riches restent de la **preuve locale non publiable**, même en cas d'échec. Le JSON final publié est reconstruit à partir de champs scalaires autorisés, jamais copié récursivement depuis le rapport : toutes les clés inconnues et leurs sous-objets (URL, headers, cookies, compte, erreurs) sont écartés. Les champs ajoutés doivent faire l'objet d'une revue de sûreté et de tests avant publication.

Les contrôles hors réseau `test_ci_artifact_publication_contract.py`, `test_ci_artifact_publication_policy.py`, `test_ci_e2e_artifact_export.py`, `test_ci_gtk_relay_artifact.py` et `test_e2e_instrumentation.py` rejettent notamment les chemins wildcard, ajout d'un upload non revu, rétention longue, secrets injectés dans des JSON imbriqués, URL/chemins personnels dans les sorties de test, fichiers inattendus, symlinks, ZIP ou cache de relais corrompus et preuves d'erreur contenant du texte libre. Les images sont traitées comme non exportables : **aucune hypothèse de fiabilité d'OCR ou d'une détection de secrets dans les pixels**.

## Historique et suppression volontaire

Les anciens artefacts Actions ont été publiés avant la politique de deux jours. Ils **ne sont pas rétroactivement nettoyés** par cette PR :

- `optimizer-gtk-e2e`, artefact `11453837989` du run `37553824555` : l'API GitHub des artefacts de ce run confirme `expired=false`, expiration **05/01/2027 00:47:45 UTC**, rétention historique d'environ 90 jours. Contient des captures/reportages d'après l'audit #1143 ; ne pas le déclarer sûr sans inspection humaine des pixels.
- `e2e-core-debug`, IDs `11697173895`, `11650278391`, `11469868845` : archives historiques mentionnées dans #1143 ; l'échantillonnage a relevé des URL de pagination publiques dans deux JSON. Leur statut courant n'a **pas** été vérifié ici, faute de route GitHub Artifacts directe exposée par le connecteur ; ne pas prétendre qu'elles ont expiré ou ont été supprimées.
- Ancien relais GTK : le workflow actif `gtk-e2e-on-approval.yml` n'archive plus le checkout source et exige une confirmation explicite ; suppression du workflow seulement à la fin de #1011.

**Procédure sans purge automatique** : le propriétaire CI/CD établit pour chaque artefact historique l'ID, le run, l'expiration, les types de fichiers et les éventuels contenus à risque ; demande une **autorisation explicite** de suppression anticipée au responsable ; exécute la suppression GitHub Actions uniquement après autorisation et avec une identité autorisée ; conserve dans #1143 la preuve (ID, action, date, sans fichier privé). À défaut, laisser l'expiration native s'appliquer et ne jamais redéposer ces archives dans un autre stockage ou ticket public.

## Limites de preuve

Les tests CI vérifient le **périmètre public effectivement construit et uploadé**, sans preuve que chaque capture locale n'ait jamais contenu de données sensibles. Les futures modifications de fixtures, screenshots ou workflows doivent conserver l'isolation synthétique et la politique de **non-publication des preuves brutes**. Toute nouvelle action d'upload, nouvelle clé dans le verdict public, ou changement de rétention exige revue explicite de #1143, même si le run est manuel.

Aucune release, aucun workflow_dispatch, aucune purge d'artefact ni modification de données XDG utilisateur n'est initié par ce dossier.
