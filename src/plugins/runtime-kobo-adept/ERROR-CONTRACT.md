# Contrat d'erreur Kobo-ADEPT / ACSM (P0 #979)

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
