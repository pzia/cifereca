## 1.2.35 — motifs ADEPT 1.3 et retryabilité des inventaires multi-profils (#979 P1)

La CLI fournit déjà une erreur typée et expurgée via `run_json()`,
mais `loan_inventories()` perdait certaines causes de contrat 1.3 :
`profile_not_found`, `adept_profile_error`,
`adept_profile_incompatible` et `adept_error` devenaient
`adept_inventory_unavailable`. Le projectionnaire de l'inventaire
conserve désormais ces quatre **codes fermés** et leurs messages
**constants**, sans reprendre `error.message`, stdout, stderr, XML,
certificats ou URL privée.

Quand l'échec provient de `KoboAdeptError`, le résultat par profil inclut
`retryable` booléen strict (seul `True` natif signifie vrai).
Les exceptions tierces `UserFacingError` n'autorisent ni transfert
du message ni inférence de retry ; elles gardent le format historique.
Les codes inconnus ou non pertinents pour l'inventaire (par exemple
`invalid_acsm`) restent `adept_inventory_unavailable`, sans champ
`retryable`. Aucune relance automatique ni changement de fulfillment
n'est ajouté ; un prêt/périmètre incomplet reste `complete=false`.

Tests : `tests/test_adept_multiprofile_inventory_error_privacy_p0.py`,
avec projection de codes, injections de secrets et processus CLI simulé.
Cette tranche n'atteste **pas** l'émission réelle par `kobo-adept-next`
des causes Adobe/libgourou et ne clôt pas #979.

---

## 1.2.30 — famille de lancement OS expurgée (#979 P0)

Un échec de lancement de sous-processus (OSError, hors FileNotFoundError)
utilisait le nom de la classe d'exception Python dans le diagnostic
`launch_error`, transmis aussi dans `KoboAdeptError.details` et
`technical_detail`. Cette valeur n'est pas un vocabulaire fermé ; un
nom de sous-classe privé pouvait ainsi traverser les exports.

La famille publiée vaut désormais toujours `launch_error=os_error`, et
le code métier demeure `kobo_adept_launch_error` avec
`retryable=false`. Ni texte de l'exception, ni argv, ni stdout/stderr,
ni secret ni opération supplémentaire n'est introduit. Le cas
`FileNotFoundError` conserve son code dédié `kobo_adept_missing`.

Test synthétique :
`tests/test_adept_os_launch_closed_p0_979.py` (OSError,
PermissionError et sous-classe portant un faux secret dans son nom).
Ce correctif ne qualifie pas la cause Adobe/libgourou côté CLI externe.

---

## 1.2.29 — timeout ACSM non réessayable sans preuve (#979 P0)

Un `subprocess.TimeoutExpired` lors de `download request` avec
`payload.source.type == "acsm"` n'est **pas** une preuve d'échec
d'activation : la licence à usage unique peut avoir été consommée
malgré le dépassement de délai. Le runtime conserve le code public
fermé `kobo_adept_timeout` mais publie désormais **`retryable=false`**
dans l'exception et le diagnostic expurgé. Les autres opérations
conservent leur politique de timeout antérieure.

Ce changement **n'annule aucun fulfillment** et ne déclenche aucune
relecture d'ACSM, aucun retry, aucun accès au compte ou changement de
broker/Core. Avant une nouvelle tentative manuelle, vérifier le reçu
et l'artefact local exact. Ne pas interpréter le timeout comme
`E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER`.

Tests hors réseau : `tests/test_adept_acsm_timeout_retry_p0_979.py`
(timeout synthétique ACSM/HTTP/Kobo/autres, données privées dans les
sorties, absence de fuite, remise à zéro des diagnostics après succès).

**Limite :** le contrat des erreurs métier Adobe de la CLI externe
reste à qualifier et #979 demeure ouvert.

---

## 1.2.28 — diagnostic fermé lorsque l'exécutable Kobo-ADEPT manque (#979 P0)

La commande `run_json()` conservait déjà le contrat public
`AttentionRequired(code="kobo_adept_missing")` en cas de
`FileNotFoundError`, mais **ne publiait aucun diagnostic structuré** :
un export pouvait simplement afficher « kobo adept », impossible à
attribuer à cette cause technique sans l'exception privée.

Le runtime publie désormais **uniquement** le code technique fermé
`kobo_adept_missing`, `retryable=false`, la famille d'opération
déjà qualifiée et la durée mesurée via `report_diagnostic`. Aucune
sortie de processus n'existe dans ce cas ; ses compteurs sont donc
omis. Le type d'exception, son code, son message utilisateur fixe et
la sémantique métier de l'appelant ne changent **pas**.

L'exception système peut mentionner le chemin du binaire, un profil,
une URL signée ou des arguments privés : ni `filename`, ni
`str(exception)`, ni `repr` ni traceback ne traversent la frontière.
Un callback de diagnostic défaillant ne modifie jamais l'échec
`AttentionRequired` ; une commande suivante réussie ne propage
aucun ancien code d'erreur.

Tests hors réseau :
`tests/test_adept_missing_binary_diagnostic_p0_979.py` (chemins
et arguments factices hostiles, callback en erreur, non-rétention).

**Limite** : cette livraison ne confirme **aucune** cause d'activation
Adobe/libgourou, n'ajoute pas de nouveau code métier fournisseur et
n'explique pas a posteriori les trois événements opaques observés sur
la machine utilisateur. Le contrat structuré réel `kobo-adept-next`
et les actions contextualisées Core/UI restent ouverts dans #979.

---

# Contrat d'erreur Kobo-ADEPT / ACSM (#979)

## Réception du contrat 1.3 — runtime 1.2.34

Le contrat amont est désormais disponible dans
[CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md](CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md)
§4, remis par [#3659](https://github.com/pzia/cifereca/pull/3659).
Il qualifie les codes structurés suivants, conservés sans interpréter le
message fournisseur :

| `error.code` | Projection |
| --- | --- |
| `invalid_acsm` | Jeton ACSM invalide |
| `profile_not_found` | Profil introuvable |
| `adept_profile_required` | Profil requis |
| `adept_profile_error` | Erreur de profil |
| `adept_profile_incompatible` | Profil incompatible |
| `adept_error` | Cause ADEPT précise non confirmée |

La même valeur est conservée dans l'exception typée et le callback
diagnostic existant. Un exit 10 ne l'efface plus pour les commandes
`profile`, `acsm`, `loans` ni pour un `download request` explicitement ACSM.
Le message affichable est local et fixe ; aucune cause n'est déduite de
stdout/stderr, du XML ou du texte d'erreur.

Hors fulfillment, seul le booléen natif `error.retryable=true` est propagé.
Une tentative de fulfillment ACSM reste **non réessayable** : le contrat
générique ne garantit pas qu'une licence à usage unique n'a pas été consommée.
Cette garde couvre aussi `acsm fetch`, y compris son timeout.
Ce drapeau n'implémente aucun retry automatique. Une réponse contradictoire
(succès avec exit non nul) et un code inconnu restent refusés comme avant.
HTTP/Kobo ne sont pas requalifiés en erreurs de licence Adobe.

Les alias legacy et le code déjà reconnu de licence attribuée à une autre
identité restent compatibles. Le contrat 1.3 ne qualifie **aucun nouveau code
détaillé libgourou**. L'historique ci-dessous décrit les anciennes limites ;
cette section prime pour les six codes documentés seulement.

Tests : `tests/test_contract_13_adept_errors.py`, avec processus simulé.
Aucune émission réelle de la CLI ni licence Adobe testée par cette réception.
La projection des actions CoreUI et la qualification de codes supplémentaires
restent distinctes. Les règles du journal brut du dépôt restent inchangées.

## État
Ce document décrit le **contrat consommé par le runtime Cifereca**, pas une
promesse que `kobo-adept-next` l'émet déjà. Validation fournisseur bloquante.

Le plugin refuse de déduire une cause Adobe depuis `message`, stdout, stderr,
XML, URI ACSM, certificats, jetons ou libgourou en texte libre.

| JSON fournisseur (champ structuré `error.code`) | Code public Cifereca | `retryable` |
| --- | --- | --- |
| `E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER` | `adept_license_already_fulfilled_by_another_user` | `false` |
| `adept_license_already_fulfilled_by_another_user` | identique | `false` |
| `adept_error` (CLI 0.13.6), ou tout code non qualifié associé à exit 10 | `adept_error` | `false` |

Les codes propres à d'autres opérations conservent le traitement historique.
L'erreur générique ne prouve **pas** une activation par une autre identité.
Une ancienne CLI reste compatible ; aucun log brut n'est analysé, même en cas
d'échec.

## Vérification nécessaire en amont

- Confirmer avec `kobo-adept-next` la version d'interface, les codes métier
  réellement émis, et leurs conditions `retryable`.
- Fournir un JSON d'erreur public nettoyé par scénario connu et par version,
  puis étendre les tables de correspondance *exactes* sans déduire de message.
- Le Core reste propriétaire de la projection contextualisée et des actions UI.
  Aucune adaptation Core dans cette PR.


## 1.2.17 — contexte d'opération et expurgation systématique

La classification Adobe s'applique **uniquement** à l'opération
`download request` dont l'entrée structurée contient
`source.type="acsm"`. Une commande `acsm return` est une
**restitution**, et un échec de profil ou un téléchargement HTTP/Kobo
ne peut être interprété comme un problème d'activation ACSM d'après
le seul code retour 10.

Pour un téléchargement ACSM en échec, **tous** les codes fournisseurs
inconnus, structurés ou mal formés, et les anciens retours 10, sont
projetés sur `adept_error` avec message public fixe et
`retryable=false`. La cause `already_fulfilled_by_another_user` n'est
retenue que si le **code machine exact** est présent et que
l'opération d'origine est bien ACSM.

Pour les opérations non ACSM, un exit 10 ou un code Adobe contradictoire
donne désormais `kobo_adept_failed` fixe, **sans** exposer le message
fournisseur. Les autres codes métier et politiques de retry hors ACSM
restent inchangés. Ce durcissement est fondé sur des scénarios
synthétiques et **ne prouve pas** que l'outil externe v0.13.6 expose
les nouvelles causes structurées. Aucune analyse de stderr, URL
signée, XML de licence ou identité Adobe n'est permise ; aucune
modification du Core/UI ni libgourou.


## 1.2.18 — fermeture des codes et messages fournisseurs non ACSM

Un `error.code` provenant de la CLI est une valeur **non fiable**, même
lorsqu'il est déclaré « structuré ». Le code historique renvoyait sans
contrôle ce champ dans `KoboAdeptError.code` pour les opérations non ACSM.
Cela pouvait transmettre des chemins, jetons, XML ou identifiants à travers
le Core, en contournant l'expurgation de `message`.

Les seuls codes non ACSM bénéficiant actuellement d'une projection publique
exacte sont `profile_missing` et `invalid_profile`, couverts par le
contrat de simulation ; leurs messages sont dorénavant **fixes** et
`retryable` reste le booléen explicite émis par la CLI. Tout code inconnu,
non textuel, contenu brut, JSON racine non qualifié ou message fournisseur
est projeté en `kobo_adept_failed` non réessayable avec texte fixe.
Les sorties `stdout`/`stderr` demeurent exclusivement des compteurs
et des diagnostics à vocabulaire fermé. La classification ACSM spécifique
de la version 1.2.17 est inchangée.

L'ajout de futurs codes nécessite une preuve du contrat public amont,
avec message fixe et politique de retry connue, et des tests de fuite.
Ce travail ne démontre aucune compatibilité nouvelle avec le binaire
`kobo-adept-next`, ni un diagnostic de la cause Adobe inconnue.


## 1.2.21 — reçus de mutation ADEPT minimisés à la frontière Core

Le runtime Kobo-ADEPT traite les réponses JSON positives du processus
externe comme des **données fournisseur non fiables**, même lorsque
`ok=true`. Auparavant `DrmMutationResult.metadata.runtime_result`
transmettait **l'objet JSON entier** à Core, y compris ses champs
additionnels potentiellement sensibles (certificat, XML de licence,
identité, lien signé, jeton). Le code transmet désormais seulement :

- **Restitution** : `ok=true`, `operation=loan_return`,
  `loan_id` et `profile` correspondant exactement à la requête
  qualifiée et validée par `return_loan` **après confirmation dans
  l'inventaire**. Core utilise ces quatre champs pour sa vérification
  indépendante suivante.
- **Fulfillment** : `ok=true`, référence optionnelle de prêt et
  profil optionnel qualifiés par type, longueur et absence de caractères
  de contrôle. L'artefact est transmis via le champ typé
  `DrmMutationResult.artifact`, et non via un objet JSON arbitraire.
  Les formats acceptés sont `epub`, `pdf`, `kepub` (ou vide
  si non renseigné) et l'empreinte optionnelle est un SHA-256
  hexadécimal valide.

Les champs fournisseur inconnus ne sont jamais copiés dans le reçu
Core, les réponses contradictoires et descripteurs de fichiers non
qualifiés sont refusés avec message/code public fixe. **Les références
de prêt, profil, destination et empreinte de fichier restent des données
nécessaires au contrat métier** et doivent continuer d'être protégées
par le Core. Les résultats intermédiaires de `download_request()`
restent accessibles uniquement au chemin d'exécution de la recette
contrôlée et nécessitent leurs propres contrôles de publication.

Ce jalon ne confirme pas l'identité Adobe réelle, ne teste pas de
licence/ACSM authentique et ne dispense pas le Core de sa vérification
d'inventaire avant de persister la restitution. Intégration CI/CD
après les versions 1.2.19 et 1.2.20 encore en revue.


## 1.2.20 — acquittement JSON strict du processus externe

Le code de retour **0** du processus ne prouve pas, à lui seul, une
réussite métier. Le runtime ne reconnaît un succès JSON que si le
document objet contient `"ok": true` **booléen exact**, sans
`error` non nul. `ok` absent, `null`, entier (`1`/`0`), chaîne
(`"true"`/`"false"`), objet ou liste, et contradiction
`{"ok":true,"error":...}` provoquent le refus stable
`kobo_adept_invalid_response` et `retryable=false`, sans jamais
reprendre le champ fournisseur, stderr, XML ou une éventuelle URI.
`{"ok":false}` et les échecs processus non nuls conservent leurs
chemins de classification existants, y compris la spécialisation
Adobe lorsque la requête ACSM est explicitement attestée.

Cette protection vaut pour `contract`, `capabilities`, la gestion
des profils, `acsm return` et `download request`. Elle ne remplace
**pas** le contrôle de l'artefact acquis ni la vérification de
l'inventaire exact après restitution ; aucune confirmation de droits
ou de succès commercial n'est inférée. Les tests reproduisent le
contrat v8 de la simulation externe avec `ok:true` et des réponses
hostiles synthétiques. L'émission du code Adobe détaillé et la
version minimale réelle de `kobo-adept-next` restent à qualifier.


## 1.2.19 — callback de diagnostic et enveloppe JSON par défaut

Le runtime évite dorénavant `exc_info=True` sur une exception émise
par `PluginContext.report_diagnostic`. Le message ou la traceback du
callback peut contenir un jeton, le contenu XML d'un ACSM, une URL de
session ou une identité. L'échec du reporter est signalé exclusivement
par le libellé fixe `Kobo-Adept diagnostic reporter unavailable` ;
il ne transforme ni une réussite ni un échec de la CLI en une autre
opération.

Chaque appel `run_json()`, y compris `contract`, `capabilities`,
`profile list/show`, `download request` et `acsm return`, possède
désormais une borne **de décodage et d'analyse JSON** de **4 Mio** par
défaut. Les budgets explicites doivent être un entier positif
inférieur ou égal à 4 Mio ; ceux dépassant cette borne ou mal formés
sont refusés **avant invocation du processus**. Le chemin historique
`loans list/expired` conserve son budget explicite de 4 Mio.
Un dépassement renvoie `kobo_adept_output_too_large`, des comptes
d'octets et une étiquette d'erreur fixe, sans inclure les flux bruts.

**Limite technique explicite** : `subprocess.run(capture_output=True)`
a déjà capturé les octets avant la vérification. Cette version ne prétend
pas borner la mémoire maximale du processus enfant ou du tampon de
capture ; cette garantie demanderait un transport incrémental supervisé
et une évolution séparée. Les tests sont synthétiques, hors ligne et
ne prouvent pas de nouveau contrat CLI amont ou de cause Adobe détaillée.


## 1.2.22 — schéma minimal de l'inventaire des prêts

La lecture `loans list/expired` ne relaie plus des objets fournisseur
`devices` imbriqués, ni les dates incidentes `fetched_at` et
`returned_at`, dont aucune n'est utilisée par le Core pour autoriser
une restitution ou choisir un prêt. Ces champs peuvent contenir des
objets de fournisseur, identifiants de terminal et secrets non typés ;
ils sont donc omis avant la frontière Core.

Le `loan.id` reste disponible **uniquement** s'il est de type texte,
non vide, de longueur maximale 256, sans espace aux extrémités, sans
caractère de contrôle et sans préfixe `-`. Les doublons dans un même
profil rendent l'inventaire ambigu : refus complet par code fixe
`kobo_adept_invalid_loan_inventory`. Un ID identique dans **deux
profils distincts** reste autorisé : l'origine du prêt demeure scindée
par profil.

Seuls `expired`, `returned`, `token_present` (booléens exacts),
ainsi que les informations optionnelles effectivement consommées par
le Core (`title` textuel borné, `validity` date ISO vérifiable et
`local_book` chemin absolu sans caractères de contrôle) sont projetés.
Les facultatifs incorrects sont omis, pas convertis en preuves
d'expiration/possession. Le chemin local exact reste disponible pour
un rapprochement explicite par le Core, jamais pour un diagnostic
public. Aucun renouvellement, restitution ni acquisition automatique.

Tests déterministes hors ligne avec données et secrets synthétiques.
Le contrat externe de codes ADEPT/libgourou et la validation sur une
machine réelle restent à établir sous #979.


## 1.2.23 — probe Core fondé sur des faits typés seulement

`probe_core()` ne retourne plus les documents JSON arbitraires
`contract` et `capabilities` fournis par le binaire Kobo-ADEPT.
La réponse au Core est reconstruite dans une table de champs fermée,
sans conserver des certificats, identités, noms de profil, URLs de
fulfillment, chemins de configuration ou clés privées éventuellement
ajoutés par un programme externe. Les indications réellement lues
par `LibraryService.kobo_adept_state` sont conservées :

- nom fixe, version numérique du contrat, version logicielle structurée
  et quatre identifiants de capacités machine exacts ;
- disponibilités booléennes strictes des transferts HTTP, ACSM et Kobo,
  formats EPUB/PDF/KEPUB publiés, et indicateurs de DRM KDRM ;
- état de restitution ADEPT (disponibilité), en conservant uniquement
  un booléen ;
- `resolved_executable` **booléen** et non plus chemin absolu pouvant
  révéler le répertoire personnel.

Les valeurs texte non typées ou inconnues ne sont pas transformées en
capacités positives. `contract()` et `capabilities()` continuent de
servir au runtime lui-même pour contrôler les actions explicites ; seul
le **probe de diagnostic publié à Core** est minimisé. Les suites hors
ligne vérifient la couverture des indicateurs Core et l'absence de
jetons, e-mails, XML, URLs et chemins dans le reçu public.

Ce changement n'est pas une preuve supplémentaire de codes d'erreur
Adobe détaillés dans une CLI réelle. #979 reste conditionné à la
qualification amont.

La projection conserve également **l’absence** d’une sous-preuve KDRM
quand aucune map n’est publiée : le consommateur Core garde alors son
fallback historique `pinned_delivery_candidate` +
`separate_content_keys_replay`. Une map explicite, même vide ou portant
`available=false`, reste prioritaire et n’est jamais remplacée par ce
fallback. Tests de la projection suivie du véritable consommateur Core.

## 1.2.24 — version de contrat bornée et commande configurée expurgée

Le runtime n'interprète plus par `int(value)` arbitraire le champ
fournisseur `contract_version`. Sont acceptés seulement un entier
natif compris entre 0 et 1000, ou, pour conserver la compatibilité
d'une CLI historique, une chaîne ASCII décimale de 1 à 3 chiffres.
Les booléens, floats, dates, objets, URL ou chaînes très longues sont
refusés par code fixe `kobo_adept_invalid_contract`. Un contrat plus
ancien que la version 8 échoue avec **message fixe sans publier la
version brute**. Aucune chaîne ou exception fournisseur n'est reprise
dans le message de la validation.

L'ancienne introspection `configured_command()` rendait à l'état
technique Core la **liste complète argv** même lorsque Kobo-Adept
était absent, révélant potentiellement un répertoire personnel,
`--token` ou une URL signée. Elle publie désormais une liste d'une
seule étiquette explicite **expurgée**. La propriété `command`
demeure inchangée pour les réelles exécutions : le diagnostic masqué
ne sert jamais de commande de lancement. Ces garde-fous s'ajoutent au
probe Core limité de la version 1.2.23, qui doit être intégrée avant
celle-ci par CI/CD.

Tests hors réseau : contrat v8 numérique ou chaîne décimale historique,
versions malformées, codes fixes et argv privé sans impact sur la
commande réellement exécutée. La qualification des véritables codes
métier Adobe/libgourou n'est toujours pas revendiquée.

Après validation, `contract()` publie une copie dont `contract_version`
est l’entier canonique. Le probe strict et le véritable consommateur Core
restent compatibles avec les chaînes historiques `"8"` et `"009"` ; le
dictionnaire original de la CLI/cache n’est pas modifié en place.


## 1.2.25 — références de restitution typées et exceptions fournisseur non chaînées

La référence de prêt ADEPT et le nom du profil sont désormais validés **avant**
toute commande de restitution : chaînes exactes non vides, au plus 256
caractères, sans conversion implicite d'objets/nombres, préfixe `-`,
blancs aux extrémités, caractère de contrôle, de format Unicode ou surrogate.
Une valeur non qualifiée produit seulement le code fixe
`adept_return_binding_required`, sans recopier l'entrée dans argv,
les journaux ou le message Core.

Après un `acsm return` confirmé par le processus, l'échec de relecture de
l'inventaire n'inclut plus l'exception ou le traceback fourni en amont :
`adept_return_verification_unavailable` est projeté avec texte fixe et
`retryable=false`, sans chaîner une erreur contenant éventuellement une
URL signée, un XML ou un identifiant. La journalisation de la phase continue
de n'utiliser que le code métier fermé. Les tests couvrent des références
malveillantes et un échec d'inventaire synthétique ; aucune restitution Adobe
réelle ni nouveau code fournisseur n'est revendiqué.


## 1.2.26 — messages expurgés dans la page Profils Adobe

La page GTK **Profils Adobe** affichait auparavant directement
`str(exception)[:240]` dans ses notifications. Cette troncature ne masque
ni le début d'une URL signée, ni une identité, un jeton ou le contenu privé
des erreurs du broker/projet externe. Les échecs de chargement, de création,
d'association et de suppression utilisent désormais seulement une
**table fermée de messages publics**, indexée par un `error.code` exact
parmi les codes déjà qualifiés. Pour tout code inconnu, de type inattendu,
ou une exception inaccessible/non sérialisable, un message fixe indique
d'utiliser Diagnostics. Aucun texte ni `repr` d'exception n'est affiché
ou journalisé par le widget. Le chemin de succès conserve ses confirmations
locales fixes.

Le présent incrément est couvert par des erreurs synthétiques contenant
des URL, adresses, certificats, chemins et éléments de licence. Il ne
requalifie pas les codes Adobe inconnus, ni les garanties du logiciel amont.


## 1.2.27 — erreurs d'inventaire multi-profils à vocabulaire fermé

L'agrégateur `loan_inventories(profiles)` continuait à exposer
`exc.code` et `safe_message(str(exc))` dans
`errors[].code/message`. Une exception structurée n'est pas
nécessairement une valeur publique : code fournisseur et message peuvent
contenir une identité, un chemin privé, une URL signée ou le XML d'une
licence. Une substitution heuristique par regex ne garantit pas
l'absence de fuite.

Les entrées d'erreur agrégées sont maintenant projetées sur une **table
fermée** de codes métier déjà reconnus, avec leurs messages constants.
Pour une erreur inconnue, non textuelle, ou dont la propriété de code
échoue, seule la paire
`adept_inventory_unavailable` / message public fixe est émise.
Aucune conversion de l'exception en texte n'est faite. Les inventaires
valides et la sémantique `complete=False` lors d'une erreur restent
inchangés. Les tests utilisent exclusivement des exceptions synthétiques
contenant jeton, URI et XML : aucune erreur de la CLI amont n'est
requalifiée, aucune opération de restitution n'est activée.


## #979 P0 — code fermé dans le diagnostic technique (`run_json`)

Le callback `PluginContext.report_diagnostic` reçoit désormais en cas
**d'échec de commande** deux champs supplémentaires : `error_code` et
`retryable`. Le code est strictement issu d'une table fermée de causes déjà
qualifiées **par le plugin** (pas du code arbitraire `error.code` du JSON
fournisseur) ; l'indicateur est le booléen décidé par cette même
classification. Exception `KoboAdeptError`, détails techniques locaux et
callback publient la **même** valeur. Une commande réussie remet ces deux
champs à l'état absent ; aucune cause précédente n'est héritée.

Cette instrumentation n'introduit **ni** parsing des logs libgourou,
**ni** code Adobe supposé, **ni** stdout/stderr, profil, identité, token,
URL ACSM, licence ou argument CLI dans le diagnostic. Les producteurs
intermédiaires Core et Diagnostics peuvent limiter la projection de ces champs :
la présence dans le callback plugin ne prouve pas l'affichage par la page
Diagnostics finale (#2380). La validation réelle de `kobo-adept-next` et
d'une licence Adobe demeure bloquante pour clore #979.

Tests synthétiques hors réseau :
`tests/test_adept_closed_diagnostic_codes_p0.py`.


## #1120 P0 — consultation `loans list` à enveloppe descriptive sans `ok`

Preuve terrain expurgée du 21/10/2026 : trois consultations `loans list`
retournent `returncode=0` mais sont refusées avec la raison fermée
`validation_reason=ok_missing` ; les commandes `contract`, `capabilities`
et `profile list` aboutissent. Le journal **n'expose pas** le JSON CLI :
il ne prouve donc pas, à lui seul, la validité interne des lignes.

Le Core connaît toutefois le **minimum stable préexistant** du contrat
`loans[]` (id unique non vide, `expired`/`returned`/`token_present`
booléens). Désormais `run_json` accepte une réponse *descriptive* sans
`ok` uniquement pour la commande exacte
`loans list --profile NAME --json`, en lecture seule, lorsque :
- le processus termine avec code 0 et sans payload stdin ;
- la réponse contient `loans` liste (0 à 5000) ; tous les éléments
  respectent les champs minimaux du contrat, les identifiants sont bornés
  et distincts, et un profil déclaré doit correspondre au profil interrogé ;
- aucun champ supérieur `ok`, `error`, `errors`, `success`, `status`
  ou `failure` n'exprime une confirmation ou un conflit ambigu.

Les champs facultatifs inconnus ne sont pas propagés par
`_loan_inventory` : seuls les éléments autorisés y sont projetés.
Une réponse partielle, contradictoire ou mal formée reste
`kobo_adept_invalid_response`, `ok_missing`, non relançable,
sans transmettre le JSON, les profils ou les livres aux diagnostics.
`loans expired`, `acsm return`, `download request` et toute autre
commande conservent **l'exigence de l'accusé `ok` booléen natif**.
Aucun retour, acquisition, prêt ni profil ne sont déclenchés par ce changement.

Tests : simulateur subprocess hors réseau + projection d'inventaire, cas
positifs/négatifs, isolation des commandes mutatrices. **Le succès auprès du
CLI réel reste à confirmer sur l'installation utilisateur, sans publier sa
sortie JSON.**
