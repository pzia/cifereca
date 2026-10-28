# #3637 — Kobo / Numilog : conformité contractuelle des acquisitions sélectionnées

Périmètre : uniquement les plugins `provider-kobo` et `provider-numilog`
(contrats parent [#3252](https://github.com/pzia/cifereca/issues/3252) /
[#3171](https://github.com/pzia/cifereca/issues/3171)). Vérification
synthétique, sans requête sur un compte réel, mutation de droit, ADEPT ou
écriture du registre Core. Les tests de la PR ciblée #3639 étendent les
régressions déjà fusionnées #3616/#3618/#3621.

## Matrice de sélection et de livraison

| Critère de recette | Kobo | Numilog |
| --- | --- | --- |
| Droit positif exact | Manifest privé acheté ; `edition_id == right_id == product_id`, `kind=purchase`, branche `personal_manifest` | Carte achetée `MesLivres` ; `edition_id == right_id == record_id`, `kind=purchase`, branche `meslivres` |
| Scope d'un même livre entre comptes | Offre opaque liée à `identity/provider/credential/account/product` ; pas de GET si réutilisée par un autre propriétaire | Offre `numilog.card:` liée à `identity/credential/account/right/product/copy/type`, session et unique AccountRef |
| Fraîcheur | Relecture du droit `rights_observation_snapshot` et POST descriptor frais avant GET ; expiration explicite refusée | Relecture `MesLivres` du droit et du lien exact avant le GET ; disparition ou signature/carte contradictoire refusée |
| 0 / 1 / N | 0 ou N candidats GET admissibles : refus ; un candidat HTTPS/DRM/format exactement qualifié : accepté ; inventaire partiel même vide | Une carte exacte et une action signée DOM-attachée nécessaires ; autres cartes ignorées seulement si non ambiguës ; inventaire partiel même vide |
| Formats admis | `http_plain` EPUB/PDF/Kepub, `adobe_adept` ACSM. Les drapeaux `family/protected/media_format/source_format` doivent concorder | `epub_direct` → candidat privé .epub, `acsm` → candidat privé .acsm. Réponse dont le format change → refus et nettoyage |
| Réponses inattendues | 3xx/4xx, HTML, fichier invalide, taille excessive : pas de candidat ; pas de redirection GET | 3xx/4xx, page login/HTML, XML non-ACSM, mauvais DRM, taille excessive : pas de candidat |
| Session et propriétaire | Broker unique, compte lié et client/epoch figés avant et pendant GET ; rebind en vol élimine le fichier | Broker unique, compte et identité vivants, client/session/provider/credential vérifiés après le staging ; changement même A→A élimine le candidat |
| Chemin cible | Dossier staging Core préexistant non symlink ; candidat temporaire seulement | Dossier staging Core préexistant non symlink, contrôlé **avant la première lecture privée** ; candidat temporaire seulement |
| Actions interdites Plugins | Ni `OriginLink`, ni placement, ni reçu final SHA, ni ADEPT Core | Ni `OriginLink`, ni placement, ni `publish()` du flux sélectionné, ni import ADEPT legacy |
| Secret et provenance | URL signée privée jamais incluse dans l'ID ni la réponse `(Path, "")` ; suivi côté Core par SourceKey | URL signée privée exclue de l'offre, trace restreinte à codes/genres ; retour `(Path, library_url)` (URL de bibliothèque stable, non signée) |

**Important :** les fonctions `rights_selected_delivery_candidate` ne
prouvent ni le consentement persistant ni la validité d'un ouvrage final ;
leur booléen `explicit_user_action` n'est qu'une barrière. Core doit
avoir approuvé le SourceKey exact, fournir le dossier privé et conserver
le contexte d'acquisition lors de la vérification/transaction.

## Tests déterministes et commandes

Pour une exécution locale dans un environnement de développement disposant
de `pytest`, `httpx` et des dépendances du dépôt :

```sh
PYTHONPATH=src:src/plugins/provider-kobo:src/plugins/provider-kobo/tests \
  python3 -m pytest -q src/plugins/provider-kobo/tests/test_p1_3252_kobo_selected_candidate.py
PYTHONPATH=src:src/plugins/provider-numilog:src/plugins/provider-numilog/tests \
  python3 -m pytest -q src/plugins/provider-numilog/tests/test_p1_3637_selected_candidate_staging.py
```

La CI `PR CI` exécute aussi les suites Plugins du dépôt et les validations
packaging. Préserver les tests existants d'offres et d'identité au lieu de
dupliquer tous leurs cas dans un seul module. N'affirmer `SUCCESS` que
pour le HEAD de PR exact et pour `main` après fusion.

## Limites de recette et handoff #3252

- Vérifier avec des comptes réels uniquement **après** autorisation explicite :
  deux comptes avec même ISBN/édition, expiration de prêt et lien signé
  réutilisé, DRM Adobe/KDRM, limites des réponses live et reprises provider.
  Les fixtures HTTP ne certifient pas l'accès réel.
- Le Core/A (#3171) reste propriétaire du consentement persistant, du
  rapprochement manuel des œuvres existantes, de l'identité ADEPT, du
  calcul SHA final, d'`Artifact/Placement/OriginLink`, de la reprise
  crash/idempotence et de la deuxième Sync 0 GET.
- Les diagnostics bruts et leur recherche/export appartiennent à C
  (#2888/#3632) ; ce lot ne modifie aucun de leurs modules.
- Le parent #3252 reste ouvert tant que ses preuves Core↔Plugins complètes
  et la recette réelle ne sont pas obtenues. Une offre absente ou un
  inventaire partiel ne constitue **jamais une preuve d'absence de droit**.
