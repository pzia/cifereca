# cifereca ↔ Kobo-Adept — contrat d’intégration complet 1.3

Révision documentaire : **1.3**  
Date : **2026-10-04**  
Baseline Kobo-Adept : **0.13.5**  
`contract_version` Kobo-Adept : **8**  
`download request` : **schema v2**  
Statut : **normatif pour l’intégration cifereca**

> Cette révision consolide en un document unique les capacités publiques Kobo-Adept nécessaires à un orchestrateur de type cifereca. Elle ne modifie pas `contract_version=8`.
>
> **Évolution 1.3 :** cette révision rebase le contrat d’intégration sur Kobo-Adept 0.13.5 et aligne la distribution Kobo-Adept sur la surface cifereca consolidée en 1.2. Elle ne change aucune sémantique consommateur : `loan_mgt`, provisioning ADEPT, ACSM, Kobo/KDRM et device restent identiques. Kobo-Adept 0.13.5 ajoute uniquement un durcissement interne du décodage des diagnostics libgourou non UTF-8.
>
> **1.3 remplace intégralement les révisions cifereca 1.0, 1.1 et 1.2.** La baseline logicielle est Kobo-Adept 0.13.5 ; `contract_version=8` et download request schema v2 restent inchangés.

---

## 0. Statut, portée et précédence

Ce document est le contrat d’intégration **cifereca ↔ Kobo-Adept**. Il décrit le sous-ensemble public de Kobo-Adept 0.13.5 que cifereca peut consommer de façon stable.

Ordre de précédence en cas d’incohérence :

```text
1. JSON Schema / sortie `contract --json` publiée par la release Kobo-Adept installée
2. comportement CLI public de cette même release
3. présent contrat cifereca 1.3
4. exemples et anciens handoffs
```

Une incohérence doit être remontée ; cifereca ne doit pas deviner silencieusement une sémantique. Les exemples de ce document sont normatifs uniquement pour les champs explicitement qualifiés de stables.

---

## 1. Objet

cifereca est l’orchestrateur fonctionnel. Kobo-Adept est un exécuteur local générique.

Le principe d’architecture est :

```text
cifereca décide
Adept exécute localement
le provider expose ses informations d’acquisition
```

Kobo-Adept ne connaît pas le modèle métier interne de cifereca et ne doit pas dépendre d’un provider particulier.

cifereca possède notamment :

- les identités fonctionnelles ;
- les comptes/provider bindings ;
- la politique de sélection des candidats ;
- le portefeuille local d’Artifacts ;
- les destinations finales ;
- l’état désiré des devices ;
- l’état des droits/prêts côté application ;
- les décisions de téléchargement, restitution, import et suppression.

Kobo-Adept possède notamment :

- les profils ADEPT/ByteBooks et leurs secrets techniques ;
- l’exécution ACSM/libgourou ;
- les opérations de retour ADEPT ;
- l’exécution locale des recettes Kobo/KDRM ;
- la détection et les opérations physiques sur les liseuses Kobo ;
- la sécurité des chemins device ;
- les sorties JSON, erreurs et diagnostics techniques.

---

## 2. Invariants

Les invariants suivants sont normatifs pour cifereca :

1. acquisition/download et import sur liseuse sont deux opérations distinctes ;
2. une commande `download` ou `acsm fetch` ne doit pas écrire sur la liseuse ;
3. une commande `device import` ne doit pas réaliser d’acquisition réseau ;
4. cifereca fournit la destination locale exacte des acquisitions ;
5. cifereca fournit un Artifact local existant pour `device import` ;
6. `provider` est une provenance opaque pour Adept ;
7. les secrets fournis en entrée ne sont pas réémis dans les résultats JSON ;
8. un résultat final n’est publié qu’après validation de l’Artifact ;
9. les appels `--json` ont un stdout contenant exactement un document JSON ;
10. les capabilities doivent être négociées ; cifereca ne déduit pas une fonction du seul numéro de version ;
11. une identité cifereca peut exister sans profil Adept et sans liseuse ;
12. les fichiers techniques ADEPT ne sont jamais lus ou modifiés directement par cifereca.

---

## 3. Découverte, version et capabilities

### 3.1 Contrat structuré

Commande :

```bash
kobo-adept contract --json
```

cifereca doit au minimum vérifier :

```text
name = kobo-adept
contract_version >= 8
```

Le contrat structuré publie également les commandes canoniques, les capabilities, la taxonomie d’erreurs et les principes de transport.

### 3.2 Capabilities d’environnement

Commande :

```bash
kobo-adept capabilities --json
```

Cette commande ne sonde pas les devices par défaut.

Pour forcer la présence matérielle :

```bash
kobo-adept capabilities --probe-devices --json
```

Le probe device canonique reste :

```bash
kobo-adept device list --json
```

### 3.3 Capabilities importantes pour cifereca

cifereca doit traiter au minimum les blocs suivants :

```text
capabilities.download.http
capabilities.download.acsm
capabilities.download.kobo
capabilities.adept
capabilities.adept.profile_provisioning
capabilities.devices
capabilities.automation
```

Pour KDRM :

```text
capabilities.download.kobo.available == true
capabilities.download.kobo.drm.KDRM.available == true
```

Pour ACSM :

```text
capabilities.download.acsm.available == true
capabilities.adept.fetch_capable_profiles contient le profil choisi
```

Pour la gestion des prêts ADEPT (`loan_mgt`) :

```text
contract.capabilities contient "adept_loans"

# inventaire local : disponible dès qu’un profil ADEPT géré peut être chargé
kobo-adept loans list --profile NAME --json
kobo-adept loans expired --profile NAME --json

# restitution réseau/libgourou
capabilities.adept.loan_return == true
```

`capabilities.adept.loan_return` correspond à la disponibilité de l’utilitaire libgourou `adept_loan_mgt`. Il ne conditionne **que l’action de retour**. L’inventaire local `loans list/expired` reste exploitable même si `loan_return=false`.

La capability textuelle `adept_loans` du contrat et le booléen runtime `capabilities.adept.loan_return` ont donc des rôles différents : la première annonce la surface de gestion des prêts ; le second indique si la restitution effective est exécutable dans l’environnement courant.

Pour le placement device configurable :

```text
capabilities.devices.import_target_directory == true
capabilities.devices.import_returns_relative_path == true
```

---

## 4. Transport JSON et erreurs

### 4.1 Succès

Les commandes publiques automatisables avec `--json` renvoient **exactement un document JSON** sur stdout. La forme de l’enveloppe dépend toutefois de la commande.

La plupart des opérations mutationnelles utilisent `ok: true`, mais cifereca **ne doit pas imposer `ok` à toutes les commandes d’inventaire**. En particulier, en 0.13.5 :

```json
{
  "loans": []
}
```

est une réponse de succès valide pour `loans list --json` et `loans expired --json`. La règle de transport est donc « un seul document JSON », pas « un champ `ok` universel ».

### 4.2 Erreur

Forme générique :

```json
{
  "ok": false,
  "error": {
    "code": "stable_code",
    "message": "texte humain",
    "retryable": false,
    "exit_code": 1
  }
}
```

cifereca doit raisonner en priorité sur `error.code`, puis sur `error.retryable`. Le texte `message` est destiné au diagnostic et ne doit pas être parsé fonctionnellement.

### 4.3 Catégories de codes de sortie

| Exit | Catégorie |
|---:|---|
| 0 | succès |
| 1 | runtime/interne |
| 2 | usage CLI |
| 3 | entrée ou Artifact invalide |
| 4 | auth/autorisation |
| 5 | source/profil/device/fichier absent |
| 6 | capability/format/DRM non supporté |
| 7 | réseau/upstream |
| 8 | stockage local |
| 9 | device |
| 10 | ADEPT/libgourou/profil |
| 11 | conflit d’état ou de destination |

Codes stables particulièrement utiles :

```text
invalid_request
invalid_acsm
invalid_epub
invalid_pdf
source_auth_expired
not_authorized
not_owned
source_not_found
rate_limited
upstream_error
download_interrupted
storage_error
destination_exists
unsupported_format
unsupported_drm
not_downloadable
profile_not_found
adept_profile_required
adept_profile_error
adept_profile_incompatible
adept_error
kobo_invalid_response
kobo_kdrm_context_required
kobo_kdrm_backend_unavailable
kobo_kdrm_error
device_not_found
device_ambiguous
device_read_only
device_file_not_found
invalid_target_directory
unsafe_target_path
cannot_create_target_directory
import_target_conflict
hook_failed
internal_error
```

---

## 5. Modèle identité cifereca / profil ADEPT

Une identité cifereca n’est pas un profil Adept.

Modèle recommandé :

```text
cifereca identity
  ├── provider accounts          0..n
  ├── adept_profile_id           0..1
  └── device bindings            0..n
```

Le profil Adept expose :

```text
profile_id       identifiant stable
name             nom humain/CLI
account          email ADE/ByteBooks éventuel
user_uuid         identité ADEPT éventuelle
complete          activation locale complète
fetch_capable     fulfillment ACSM possible
provisioning.*    état UI-oriented
devices[]         devices associés
```

cifereca doit persister `profile_id` comme référence externe stable. Le `name` reste nécessaire à la CLI 0.13.5 et doit donc également être conservé ou résolu au moment de l’appel.

### 5.1 Lister / inspecter

```bash
kobo-adept profile list --json
kobo-adept profile show NAME --json
```

### 5.2 Création d’un profil host ADEPT — chemin nominal cifereca

Pour créer un profil ADEPT local utilisable pour ACSM, le chemin automatisable recommandé en 0.13.5 est :

```bash
kobo-adept profile add NAME \
  --username EMAIL_ADE \
  --json
```

Cette opération crée le profil et lance l’activation ADEPT/ByteBooks locale.

Critères utiles ensuite :

```text
profile.complete == true
profile.fetch_capable == true
profile.provisioning.acsm_fetch_ready == true
```

### 5.3 Profil vide

La commande :

```bash
kobo-adept profile create NAME --json
```

crée seulement un conteneur vide. Elle ne réalise aucune activation réseau.

En 0.13.5, compléter ensuite ce même shell par :

```bash
kobo-adept account init --profile NAME --username EMAIL_ADE
```

est possible, mais `account init` est une interface de compatibilité sans sortie JSON contractuelle. cifereca ne doit donc pas choisir ce chemin comme workflow d’automatisation principal tant qu’une primitive JSON équivalente n’est pas publiée.

### 5.4 Association d’une liseuse déjà autorisée

Une fois le profil host complet, le chemin nominal cifereca pour rattacher une Kobo déjà autorisée avec la même identité ADEPT est :

```bash
kobo-adept profile associate NAME \
  --mount /media/$USER/KOBOeReader \
  --json
```

`profile associate` :

- n’importe pas le profil ADE depuis la liseuse ;
- vérifie la compatibilité de l’identité ADEPT ;
- associe le device au profil ;
- échoue si l’identité du device est incompatible.

L’erreur fonctionnelle attendue en cas d’incompatibilité est notamment :

```text
adept_profile_incompatible
```

### 5.5 Adoption/import depuis la Kobo

Les commandes :

```bash
kobo-adept profile adopt-kobo NAME --mount PATH --json
kobo-adept profile import-kobo NAME --mount PATH --json
```

existent mais ne constituent **pas le chemin nominal de provisioning cifereca**.

cifereca doit préférer :

```text
profil host ADEPT complet
→ Kobo autorisée avec la même identité
→ profile associate
```

L’adoption/import reste une capability alternative ou de diagnostic.

### 5.6 Suppression

```bash
kobo-adept profile remove NAME --json
```

`--force` ne doit pas être utilisé automatiquement dans l’UI normale lorsqu’un device est encore associé.

---

## 6. Interface d’acquisition unifiée

Commande recommandée pour une intégration automatisée :

```bash
kobo-adept download request - --json
```

Le JSON est fourni sur stdin.

Baseline :

```text
schema_version = 2
source.type = http | acsm | kobo
```

La destination est un chemin exact appartenant à cifereca.

### 6.1 Résultat commun

Exemple :

```json
{
  "ok": true,
  "operation": "download",
  "artifact": {
    "path": "/home/user/EBooks/book.epub",
    "format": "epub",
    "drm": "none",
    "size": 123456,
    "sha256": "...64 hex..."
  },
  "metadata": {},
  "hook": {
    "status": "skipped"
  },
  "profile": "heloise",
  "loan_id": "urn:uuid:..."
}
```

`profile` et `loan_id` sont présents lorsque l’opération les produit.

En 0.13.5, il n’existe pas encore de modèle contractuel complet `readable`, `drm_input`, `drm_output`, `transformations`. cifereca ne doit pas inventer ces champs avant une version Adept qui les publie.

---

## 7. HTTP direct

Un contenu réellement sans DRM ou directement exploitable peut être acquis via :

```json
{
  "schema_version": 2,
  "source": {
    "type": "http",
    "provider": "provider-opaque",
    "url": "https://example/book.epub",
    "headers": {}
  },
  "destination": "/home/user/EBooks/book.epub",
  "format": "epub"
}
```

Commande simplifiée disponible :

```bash
kobo-adept download url URL \
  --output FILE \
  --format epub \
  --json
```

### Invariant DRM

cifereca ne doit jamais aplatir un candidat connu `kobo_kdrm` en téléchargement HTTP direct.

```text
http_plain  → HTTP direct possible
kobo_kdrm   → chemin Kobo/KDRM obligatoire
adobe_adept → chemin ACSM/ADEPT
```

L’ordre de préférence entre plusieurs candidats compatibles appartient à cifereca.

---

## 8. ACSM / ADEPT et `loan_mgt`

### 8.1 Acquisition ACSM

Commande publique :

```bash
kobo-adept acsm fetch FILE_OR_URL \
  --profile NAME \
  --output /chemin/exact/book.epub \
  --json
```

La source peut être un fichier local ou une URL HTTP(S). Des headers HTTP peuvent être ajoutés avec `--header NAME:VALUE`.

Équivalent via `download request` :

```json
{
  "schema_version": 2,
  "source": {
    "type": "acsm",
    "provider": "provider-opaque",
    "url": "https://example/control.acsm",
    "headers": {}
  },
  "destination": "/home/user/EBooks/book.epub",
  "format": "epub",
  "profile": "heloise"
}
```

Le profil doit être `fetch_capable`. cifereca ne lit ni `activation.xml`, ni `device.xml`, ni les clés ADEPT.

### 8.2 Rôle de `loan_mgt`

Kobo-Adept encapsule deux éléments distincts :

```text
registre local de prêts Kobo-Adept
    +
utilitaire libgourou adept_loan_mgt pour le retour effectif
```

cifereca ne doit **jamais** invoquer directement `adept_loan_mgt`, lire `adept/loans/*.xml` ou modifier `loans-state.json`. La surface publique est la CLI Kobo-Adept.

Surface automatisable 0.13.5 :

```bash
kobo-adept loans list --profile NAME --json
kobo-adept loans expired --profile NAME --json
kobo-adept acsm return LOAN_ID --profile NAME --json
```

Surface expert/historique :

```bash
kobo-adept loans return LOAN_ID --profile NAME
```

`loans return` n’a pas de `--json` en 0.13.5 ; cifereca ne l’utilise donc pas pour l’automatisation.

### 8.3 Inventaire des prêts

Commande canonique :

```bash
kobo-adept loans list --profile heloise --json
```

Forme réelle 0.13.5 :

```json
{
  "loans": [
    {
      "id": "loan-123",
      "title": "Titre du livre",
      "validity": "2026-10-25T21:59:59Z",
      "local_book": "/home/user/EBooks/Titre.epub",
      "fetched_at": "2026-10-03T19:30:00Z",
      "expired": false,
      "returned": false,
      "token_present": true,
      "devices": []
    }
  ]
}
```

Sous-ensemble stable consommé par cifereca :

```text
loans[].id             loan_id ADEPT
loans[].expired        booléen calculé depuis validity
loans[].returned       booléen dérivé de returned_at local
loans[].token_present  présence actuelle du token réduit suivi par Kobo-Adept
```

Champs optionnels utiles :

```text
title
validity
local_book
fetched_at
returned_at
devices[]
```

Des champs techniques supplémentaires (`operator_url`, `token_path`, `discovered_at`, etc.) peuvent être présents. cifereca les ignore fonctionnellement et accepte les propriétés additionnelles.

`token_present=false` ne signifie pas à lui seul « retour impossible » ni « supprimer l’historique ». L’entrée locale peut rester utile à la réconciliation et au diagnostic.

### 8.4 Prêts expirés

```bash
kobo-adept loans expired --profile NAME --json
```

La sortie conserve la même enveloppe `{"loans": [...]}` mais ne contient que les entrées :

```text
expired == true
returned == false
```

Cette commande est un **inventaire**, pas une action de retour. cifereca décide séparément de sa politique métier pour les prêts expirés.

### 8.5 Capture du `loan_id` lors du fulfillment

Avant et après un fulfillment ACSM, Kobo-Adept compare les tokens de prêt connus. Lorsqu’un unique nouveau prêt peut être attribué à l’Artifact produit, il enregistre le lien technique et le résultat d’acquisition peut exposer :

```json
{
  "profile": "heloise",
  "loan_id": "loan-123"
}
```

`loan_id` est **optionnel** : un ACSM peut ne pas être un prêt, aucun nouveau token peut apparaître, ou plusieurs tokens peuvent empêcher une attribution sûre.

Lorsque `loan_id` est présent, cifereca persiste au minimum :

```text
artifact_id
adept_profile_id
adept_profile_name
loan_id
provider/account provenance
état métier du prêt
```

Le couple nécessaire à la restitution ADEPT en 0.13.5 est :

```text
profile name + loan_id
```

Le `loan_id` ADEPT reste distinct de tout identifiant de prêt du provider.

### 8.6 Réconciliation `Artifact ↔ loan_id`

Si cifereca ne possède pas le `loan_id`, il peut le reconstruire à partir de l’état Kobo-Adept :

```text
1. résoudre le profil ADEPT lié à l’Artifact / identité ;
2. loans list --profile NAME --json ;
3. comparer local_book au chemin canonique exact de l’Artifact ;
4. accepter uniquement une correspondance unique ;
5. persister loans[].id comme loan_id ;
6. ne jamais deviner par titre, ISBN, product_id, revision_id ou identifiant provider.
```

Si aucune correspondance unique n’existe, l’action de retour reste **non résolue**. cifereca peut présenter une sélection explicite à l’utilisateur, mais ne choisit jamais silencieusement un prêt ambigu.

### 8.7 Restitution anticipée

Précondition d’environnement :

```text
capabilities.adept.loan_return == true
```

Commande canonique :

```bash
kobo-adept acsm return LOAN_ID \
  --profile NAME \
  --json
```

Succès :

```json
{
  "ok": true,
  "operation": "loan_return",
  "profile": "heloise",
  "loan_id": "loan-123"
}
```

Règles cifereca :

- utiliser le profil ADEPT d’origine du fulfillment ;
- utiliser le `loan_id` ADEPT exact ;
- ne jamais substituer un identifiant provider ;
- si le `loan_id` manque, exécuter d’abord la réconciliation 8.6 ;
- ne marquer le prêt `returned` côté métier qu’après succès de la commande ;
- en cas d’échec, conserver l’état précédent et l’Artifact.

Après succès, Kobo-Adept appelle `adept_loan_mgt`, puis marque localement l’entrée avec `returned_at`. Un rafraîchissement :

```bash
kobo-adept loans list --profile NAME --json
```

doit alors exposer `returned=true` pour l’entrée correspondante.

### 8.8 Association du prêt lors du transfert vers une Kobo

`device import` sait associer un transfert à un prêt suivi :

```bash
kobo-adept device import FILE \
  --profile NAME \
  --mount PATH \
  --loan-id LOAN_ID \
  --json
```

Si `--loan-id` est omis, Kobo-Adept tente de retrouver **une correspondance unique** à partir du chemin local du livre déjà enregistré dans son état de prêt. cifereca doit néanmoins fournir `--loan-id` lorsqu’il le connaît : cela rend le lien explicite et évite toute dépendance à une ancienne corrélation locale.

Le résultat `device_import` peut alors retourner :

```json
{
  "loan_id": "loan-123",
  "loan_context_changed": true
}
```

et Kobo-Adept mémorise le device/target pour ce prêt. Cette information permet ensuite le nettoyage ciblé des copies de prêts retournés ou expirés.

### 8.9 Nettoyage optionnel du device après retour/expiration

Le **retour ADEPT** et la **suppression physique sur la Kobo** sont deux opérations distinctes.

Pour inventorier les fichiers de prêts retournés/expirés suivis sur une Kobo :

```bash
kobo-adept cleanup \
  --profile NAME \
  --mount PATH \
  --json
```

Sans `--apply`, la commande est un dry-run. Forme 0.13.5 :

```json
{
  "profile": "heloise",
  "apply": false,
  "candidates": [
    {
      "loan_id": "loan-123",
      "title": "Titre",
      "expired": false,
      "returned": true,
      "target": "/media/user/KOBOeReader/Digital Editions/Titre.epub",
      "exists": true
    }
  ]
}
```

Suppression effective :

```bash
kobo-adept cleanup \
  --profile NAME \
  --mount PATH \
  --apply \
  --json
```

cifereca ne doit pas lancer `--apply` implicitement lors d’un retour. La politique recommandée est : retour du droit d’abord, puis proposition séparée de retrait de la liseuse.

### 8.10 Effets fonctionnels et séparation d’état

Un retour réussi modifie le droit ADEPT et l’état technique local Kobo-Adept, mais ne supprime pas automatiquement :

```text
l’Artifact local cifereca
la copie éventuelle sur la Kobo
l’enregistrement métier du provider
```

cifereca garde séparés :

```text
état ADEPT/Kobo-Adept : active | expired | returned
état provider         : selon le provider
Artifact local        : present | missing | invalid
placement device      : present | missing | unknown
```

### 8.11 Erreurs et cas incomplets

Si `capabilities.adept.loan_return == false`, cifereca continue à utiliser `loans list/expired` mais désactive la restitution ADEPT.

Si `loans list` ne permet pas de résoudre un `loan_id`, cifereca ne fabrique pas d’identifiant. L’UI peut afficher :

```text
prêt ADEPT détecté / restitution non résolue
```

Un échec de `acsm return` ne supprime ni l’Artifact, ni le binding de prêt, ni la copie device.

### 8.12 Compatibilité de schéma 0.13.5

Kobo-Adept 0.13.5 publie `loan-return-result.schema.json`, mais pas de `loan-list.schema.json` ni de schéma dédié pour `cleanup --json`.

Pour cifereca 1.3, les sous-ensembles stables consommés sont :

```text
loans list/expired:
  loans[].id
  loans[].expired
  loans[].returned
  loans[].token_present

cleanup:
  profile
  apply
  candidates[].loan_id
  candidates[].expired
  candidates[].returned
  candidates[].target
  candidates[].exists
```

cifereca accepte les champs supplémentaires. Une future release Kobo-Adept pourra publier des JSON Schema dédiés sans modifier cette sémantique minimale.

---

## 9. Kobo delegated / KDRM

### 9.1 Principe

Pour un contenu KDRM, cifereca transmet à Adept une recette Kobo. Adept rejoue localement la requête content access afin d’obtenir les `ContentKeys` et réalise le traitement KDRM localement.

Les valeurs des `ContentKeys` ne doivent jamais être injectées directement dans la requête par cifereca ou un provider.

### 9.2 Requête type avec candidat épinglé

```json
{
  "schema_version": 2,
  "source": {
    "type": "kobo",
    "provider": "kobo-vault",
    "request": {
      "url": "https://storeapi.kobo.com/.../access",
      "method": "GET",
      "headers": {
        "Authorization": "Bearer ..."
      },
      "query": {
        "DisplayProfile": "Android"
      }
    },
    "candidate": {
      "url": "https://storedownloads.kobo.com/...",
      "headers": {
        "x-amz-request-payer": "requester"
      },
      "drm_type": "KDRM",
      "media_format": "epub",
      "source_format": "FLEPUB",
      "delivery_source": "library_sync",
      "platform": "Android"
    },
    "kdrm": {
      "device_id": "...",
      "user_id": "..."
    }
  },
  "destination": "/home/user/EBooks/book.epub",
  "format": "epub"
}
```

### 9.3 Séparation livraison / clés

Pour KDRM :

```text
source.candidate.url
    = payload à télécharger

source.request
    = requête rejouée localement pour obtenir les ContentKeys
```

La réponse de `source.request` peut ne contenir aucune URL de téléchargement si le candidat de livraison a été fourni séparément.

### 9.4 Context KDRM

Requis :

```text
source.kdrm.device_id
source.kdrm.user_id
```

Ce contexte est Kobo ; il ne s’agit pas d’un profil ADEPT.

### 9.5 DRM supporté sur le chemin Kobo

| DRM Kobo | 0.13.5 |
|---|---|
| SignedNoDrm / NoDrm | supporté |
| KDRM | supporté localement |
| AdobeDrm | non supporté via `source.type=kobo` |

Pour `AdobeDrm`, cifereca doit essayer un candidat ACSM/ADEPT ou un autre candidat compatible.

### 9.6 Redirections et secrets

Adept supprime les secrets sensibles tels que `Authorization`, `Cookie` et `Proxy-Authorization` lors des redirections cross-origin. `x-amz-request-payer` peut être conservé lorsqu’il fait partie de la recette.

---

## 10. Sélection des candidats côté cifereca

Adept exécute un candidat ; il n’est pas le planner métier.

cifereca doit classifier au minimum :

```text
handling=http_plain
handling=adobe_adept
handling=kobo_kdrm
handling=unknown
```

Mapping obligatoire :

```text
http_plain
  → source.type=http ou Kobo sans DRM compatible

adobe_adept
  → source.type=acsm + profil ADEPT fetch-capable

kobo_kdrm
  → source.type=kobo + recette delegated/pinned + contexte KDRM

unknown
  → ne pas marquer complete sans preuve d’un Artifact valide
```

Jamais :

```text
kobo_kdrm → descriptor-http → status=complete
```

cifereca peut définir son ordre de préférence propre. Le contrat Adept n’impose pas l’ordre entre ACSM et KDRM lorsqu’ils sont tous les deux disponibles.

---

## 11. Device Kobo

### 11.1 Découverte

```bash
kobo-adept device list --json
kobo-adept device info DEVICE_ID --json
kobo-adept device books DEVICE_ID --json
```

La découverte automatique ne doit pas être remplacée par un scan récursif de `/media`, `/run/media` ou GVFS côté cifereca.

### 11.2 Présence

Commande canonique :

```bash
kobo-adept device presence DEVICE_ID \
  --path RELATIVE_PATH \
  --json
```

Autres sélecteurs disponibles : `--filename`, `--sha256`, `--content-id`.

### 11.3 Import d’un Artifact local

```bash
kobo-adept device import FILE \
  --mount PATH \
  --json
```
Pour un Artifact issu d’un prêt ADEPT connu, cifereca peut rendre le lien explicite :

```bash
kobo-adept device import FILE \
  --profile NAME \
  --mount PATH \
  --loan-id LOAN_ID \
  --json
```

Formats supportés : EPUB et PDF. L’import reste distinct de toute acquisition distante. Le `loan_id` n’est pas nécessaire pour un livre non prêté ; il sert au suivi du contexte de prêt et au nettoyage ultérieur.

### 11.4 Placement relatif piloté par cifereca

```bash
kobo-adept device import FILE \
  --mount PATH \
  --target-dir 'Books/cifereca' \
  --json
```

`--target-dir` :

- est relatif au mount Kobo ;
- ne doit pas être absolu ;
- ne doit pas contenir `..` ;
- ne doit pas sortir du mount après résolution ;
- ne doit pas s’échapper via symlink ;
- ne doit pas viser un répertoire technique réservé ;
- peut être créé par Adept s’il n’existe pas et que le device est inscriptible.

Succès :

```json
{
  "ok": true,
  "operation": "device_import",
  "source": "/home/user/EBooks/book.epub",
  "target": "/media/user/KOBOeReader/Books/cifereca/book.epub",
  "target_relative_path": "Books/cifereca/book.epub",
  "requested_target_directory": "Books/cifereca"
}
```

cifereca doit persister `target_relative_path` comme emplacement observé canonique.

### 11.5 Suppression

```bash
kobo-adept device remove \
  'Books/cifereca/book.epub' \
  --mount PATH \
  --apply \
  --json
```

La suppression device ne supprime jamais par elle-même l’Artifact local ni le droit distant.

### 11.6 Réconciliation device

Modèle recommandé cifereca :

```text
DesiredPlacement
  device_id
  artifact_id
  state = keep | remove | inherit
  target_directory

ObservedPlacement
  device_id
  artifact_id
  state = present | missing | unknown
  target_relative_path
  observed_at / sync_id
```

Règles :

```text
desired=keep + observed=present → rien
desired=keep + observed=missing → device import Artifact local
desired=remove + observed=present → device remove target_relative_path
```

Retirer puis remettre un livre sur la Kobo ne doit pas redéclencher un ACSM si l’Artifact local valide existe encore.

### 11.7 Nettoyage des copies de prêts retournés/expirés

Le nettoyage piloté par le registre de prêts est disponible via `kobo-adept cleanup`. Il ne remplace pas `device remove` : `cleanup` calcule ses candidats depuis le suivi `loan_mgt`, alors que `device remove` cible explicitement un chemin relatif choisi par cifereca.

```bash
kobo-adept cleanup --profile NAME --mount PATH --json          # dry-run
kobo-adept cleanup --profile NAME --mount PATH --apply --json  # suppression
```

cifereca doit présenter la suppression device comme une décision distincte du retour du prêt.

---

## 12. Autorisation ADEPT du device

Kobo-Adept expose :

```bash
kobo-adept device authorize \
  --profile NAME \
  --mount PATH \
  --json
```

Cette opération est distincte de `profile associate` :

- `associate` vérifie/référence une Kobo déjà autorisée avec la même identité ;
- `device authorize` tente de modifier l’autorisation ADEPT de la Kobo.

cifereca ne doit pas lancer automatiquement une réautorisation destructrice ou `--force` sans action utilisateur explicite.

---

## 13. Hook post-download

Kobo-Adept peut exécuter un hook post-download configuré localement.

Le hook :

- peut modifier l’Artifact ;
- a stdout/stderr capturés ;
- ne pollue pas stdout JSON ;
- entraîne un recalcul final taille/SHA-256 après mutation ;
- peut recevoir le profil via `{profile}` / `KOBO_ADEPT_PROFILE`.

Commande de test :

```bash
kobo-adept hook test FILE --json
```

Important : en 0.13.5, le retrait arbitraire de DRM n’est **pas** une capability publique générique. cifereca doit consommer le résultat final publié par Adept sans supposer l’existence d’une API publique `remove-drm FILE`.

---

## 14. Logs et diagnostic

Journal CLI persistant :

```text
${XDG_STATE_HOME:-~/.local/state}/kobo-adept-next/logs/cli.jsonl
```

Commandes :

```bash
kobo-adept logs path --json
kobo-adept logs show --limit 100 --json
kobo-adept logs show --errors --limit 100 --json
kobo-adept logs tail --limit 20 --json
```

Le journal persistant :

- n’enregistre pas argv brut ;
- n’enregistre pas les headers/credentials ;
- redige les messages sensibles ;
- journalise succès/échec, durée, commande et code d’erreur.

Pour le support cifereca, préférer la collecte des sorties JSON et des logs sanitizés plutôt que des tokens ou recettes brutes.

---

## 15. Configuration

Commandes utiles :

```bash
kobo-adept config path --json
kobo-adept config show --json
kobo-adept config explain DOTTED.KEY --json
```

Ordre de précédence :

```text
defaults
< legacy config.toml compatible
< functional.toml
< local.toml
< CLI
```

cifereca ne doit pas dépendre du chemin interne du venv ou d’un checkout source.

---

## 16. Distribution / update / rollback

Launchers stables :

```text
~/.local/bin/kobo-adept
~/.local/bin/kobo-adept-next
~/.local/bin/kobo-adept-update
```

Commandes :

```bash
kobo-adept-update RELEASE.zip --json
kobo-adept-update RELEASE.zip --dry-run --json
kobo-adept-update --status --json
kobo-adept-update --rollback --json
```

Le modèle de distribution utilise des releases immuables avec venv privé, `current` atomique et `previous` pour rollback.

cifereca ne doit jamais demander à l’utilisateur d’activer le venv Kobo-Adept.

---

## 17. État minimal à persister côté cifereca

### 17.1 Profil

```text
adept_profile_id
adept_profile_name
last_seen_complete
last_seen_fetch_capable
```

### 17.2 Acquisition

```text
artifact_id
provider_id / credential_id / account_id
source handling
artifact.path
artifact.format
artifact.sha256
artifact.drm
profile_id/name si ACSM
loan_id si présent
acquisition status
```

### 17.3 Prêt

```text
loan_id
adept_profile_id/name
artifact_id / local_book binding si connu
provider loan reference éventuelle, séparée du loan_id ADEPT
state = active | returned | expired | unknown
validity éventuelle
token_present éventuel
returned_at / expired_at éventuel
last_reconciled_at éventuel
```

`loan_id` peut être récupéré/revalidé depuis `kobo-adept loans list --profile NAME --json`. L’état cifereca reste l’état métier autoritatif de l’application ; l’inventaire Adept est une source technique de réconciliation.

### 17.4 Device

```text
device_id
identity_id
target_directory
observed target_relative_path
last observed state
```

cifereca ne doit jamais persister les `ContentKeys`, private keys ADEPT, bearer Kobo ou contenus de `activation.xml`.

---

## 18. Workflows de référence

### 18.1 Identité sans ADEPT

```text
créer identité cifereca
→ rattacher comptes/provider
→ adept_profile_id = null
```

Adept n’est pas requis.

### 18.2 Nouveau profil ADEPT host + Kobo

```text
cifereca
→ profile add NAME --username EMAIL --json
→ vérifier complete/fetch_capable
→ utilisateur autorise ou possède une Kobo avec la même identité ADE
→ profile associate NAME --mount PATH --json
→ cifereca persiste profile_id + binding device
```

### 18.3 Acquisition ACSM

```text
cifereca obtient ACSM
→ choisit profil ADEPT fetch-capable
→ acsm fetch / download request
→ Adept produit EPUB/PDF
→ résultat artifact + profile + loan_id éventuel
→ cifereca persiste l’Artifact et le droit
```

### 18.4 Retour anticipé

```text
utilisateur clique « Restituer »
→ cifereca cherche loan_id + profil ADEPT d’origine
→ si loan_id manque : loans list --profile NAME --json
→ corrélation unique via local_book / Artifact path
→ acsm return LOAN_ID --profile NAME --json
→ succès
→ refresh loans list (returned=true)
→ cifereca marque le droit returned
→ Artifact local conservé selon politique cifereca
→ optionnel : cleanup --json pour proposer le retrait device
→ cleanup --apply uniquement après décision explicite
```

### 18.5 Reconstruction des prêts au démarrage

Pour chaque profil ADEPT utile à cifereca :

```text
loans list --profile NAME --json
→ fusionner par (profile, loan_id)
→ rattacher à l’Artifact si local_book correspond exactement
→ mettre à jour expired / returned / validity / token_present
→ ne pas supprimer les prêts historiques absents de la liste sans politique explicite
```

Ce mécanisme permet notamment de restituer un prêt même si cifereca n’avait pas persisté le `loan_id` au moment du téléchargement, à condition que Kobo-Adept ait pu le suivre localement.

### 18.6 KDRM

```text
provider retourne handling=kobo_kdrm
→ cifereca demande/obtient la recette delegated
→ cifereca construit source.type=kobo
→ Adept rejoue content_access pour ContentKeys
→ télécharge le candidat épinglé
→ traite KDRM localement
→ Artifact final
```

Jamais de `descriptor-http` pour KDRM.

### 18.7 Synchronisation liseuse

```text
Artifact local valide
→ DesiredPlacement
→ device import --target-dir
→ target_relative_path
→ ObservedPlacement
→ presence/remove ultérieurs avec le même chemin relatif
```

---

## 19. Limitations explicites de la baseline 0.13.5

1. `contract_version` reste 8 ; les ajouts compatibles sont négociés par capabilities.
2. Il n’existe pas encore de modèle final `readable/drm_input/drm_output/transformations`.
3. `profile create` + activation ultérieure d’un shell existant n’a pas de primitive JSON dédiée ; pour une création automatisée neuve, cifereca préfère `profile add`.
4. Le `contract.json` 0.13.5 présente encore `adopt-kobo` comme workflow reader-backed ; cifereca **ne l’utilise pas comme workflow nominal**. Le chemin retenu est profil host complet → Kobo autorisée avec la même identité → `profile associate`.
5. `loan_id` peut être absent du résultat ACSM ; cifereca tente alors `loans list --profile NAME --json` et une corrélation unique par `local_book`.
6. `loans list/expired --json` n’ont pas de JSON Schema séparé et n’utilisent pas d’enveloppe `ok`; le sous-ensemble stable est défini en section 8.12.
7. `kobo-adept loans return` n’est pas JSON en 0.13.5 ; cifereca utilise `acsm return ... --json`.
8. `cleanup --json` n’a pas de schéma dédié et ne doit jamais être assimilé au retour ADEPT lui-même.
9. La progression intermédiaire n’est pas contractuelle ; le résultat final est autoritatif.
10. Le runtime libgourou n’est pas encore géré comme composant versionné/rollbackable par Adept ; cette évolution appartient à une roadmap ultérieure.

---

## 20. Critères d’acceptation cifereca

L’intégration est conforme lorsque :

- [ ] cifereca lit `contract --json` et `capabilities --json` ;
- [ ] aucune fonction n’est activée uniquement à partir du numéro de version ;
- [ ] une identité cifereca peut exister sans profil Adept ;
- [ ] un profil ADEPT peut être créé via `profile add --json` ;
- [ ] une Kobo déjà autorisée peut être liée via `profile associate --json` sans import de son profil ;
- [ ] HTTP direct, ACSM et Kobo/KDRM sont routés par des mécanismes distincts ;
- [ ] `kobo_kdrm` n’est jamais téléchargé comme simple HTTP et marqué `complete` ;
- [ ] cifereca choisit un profil `fetch_capable` pour ACSM ;
- [ ] cifereca persiste `loan_id` lorsqu’il est renvoyé ;
- [ ] cifereca peut inventorier les prêts avec `loans list --profile NAME --json` ;
- [ ] cifereca peut inventorier les prêts expirés avec `loans expired --profile NAME --json` ;
- [ ] cifereca accepte que `loans list/expired --json` n’expose pas de champ `ok` en 0.13.5 ;
- [ ] si `loan_id` manque dans sa base, cifereca peut le réconcilier par correspondance unique `local_book ↔ Artifact path` ;
- [ ] aucune corrélation de prêt n’est devinée par titre/ISBN/product_id ;
- [ ] un prêt ADEPT peut être restitué via `acsm return LOAN_ID --profile NAME --json` lorsque `capabilities.adept.loan_return=true` ;
- [ ] après restitution, `loans list` peut confirmer `returned=true` ;
- [ ] un `device import` de prêt peut transmettre `--loan-id` lorsque cifereca le connaît ;
- [ ] `cleanup --json` est traité comme un dry-run distinct du retour ;
- [ ] `cleanup --apply` n’est exécuté qu’après décision explicite de suppression device ;
- [ ] le retour d’un prêt ne supprime pas implicitement l’Artifact local ;
- [ ] `device import` reste séparé de l’acquisition ;
- [ ] le placement relatif est négocié par capability ;
- [ ] `target_relative_path` est réutilisé directement pour `presence` et `remove` ;
- [ ] les erreurs sont traitées par code structuré ;
- [ ] les secrets ne sont ni journalisés par cifereca ni attendus dans les résultats Adept ;
- [ ] les launchers stables sont utilisés, sans dépendance au venv interne.

---

## 21. Références publiques Kobo-Adept 0.13.5

Les interfaces de référence de la baseline sont :

```text
kobo-adept contract --json
kobo-adept capabilities --json
interfaces/download-request.schema.json
interfaces/download-result.schema.json
interfaces/profile-list.schema.json
interfaces/profile-detail.schema.json
interfaces/profile-provision-result.schema.json
interfaces/loan-return-result.schema.json
# baseline 0.13.5: pas de loan-list.schema.json ni cleanup schema séparé
kobo-adept loans list --profile NAME --json
kobo-adept loans expired --profile NAME --json
kobo-adept acsm return LOAN_ID --profile NAME --json
kobo-adept cleanup --profile NAME --mount PATH --json
interfaces/device-list.schema.json
interfaces/device-books.schema.json
interfaces/device-presence.schema.json
interfaces/device-import-result.schema.json
interfaces/device-remove-result.schema.json
interfaces/error.schema.json
interfaces/capabilities.schema.json
```

En cas d’écart entre un exemple de ce document et un schéma JSON publié par la même release, cifereca doit signaler l’incohérence au lieu de deviner silencieusement la sémantique.

---

## 22. Versionnement de ce contrat cifereca

```text
1.0  consolidation initiale cifereca
1.1  exposition de loan_mgt / restitution et réconciliation des prêts
1.2  version complète autonome + cycle de prêt device/cleanup + corrections transport JSON
1.3  rebase sur Kobo-Adept 0.13.5 + alignement documentaire/distribution, sans changement de surface
```

La révision cifereca est indépendante du `contract_version` Kobo-Adept. Un changement de ce document n’implique pas une nouvelle release logicielle. cifereca doit toujours négocier les capabilities de la release réellement installée.

---

## 23. Résumé normatif

```text
CIFERECA
────────
Décide : identité, provider, candidat, destination, prêt, placement, UX.
Persiste : Artifact, provenance, profile_id/name, loan_id, état/réconciliation de prêt, device placement.

KOBO-ADEPT
──────────
Exécute : HTTP, ACSM/ADEPT, inventaire/retour de prêts (`loan_mgt`), Kobo/KDRM, device.
Possède : profils ADEPT, secrets techniques, libgourou, sécurité device.

RÈGLES CLÉS
───────────
ACSM       → profil ADEPT fetch-capable
loan_mgt   → loans list/expired pour découvrir et réconcilier
retour     → loan_id + même profil ADEPT + acsm return --json
prêt/device→ device import --loan-id quand connu
cleanup    → dry-run séparé, --apply uniquement sur décision explicite
KDRM       → source.type=kobo / delegated, jamais HTTP direct
liseuse    → Artifact local puis device import
placement  → target-dir relatif, target_relative_path retourné
```