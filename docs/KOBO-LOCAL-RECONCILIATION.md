# Réconciliation locale Kobo confirmée

> **Document historique/partiel — 28/10/2026.** rattrapage Kobo ponctuel, pas un prérequis universel de la Sync. La cible normative est la **synchronisation automatique unique** décrite dans [ARCHITECTURE.md](ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) ; le rattrapage manuel #2797 reste exceptionnel. Conserver les gardes existants ; ne pas déduire qu'un OriginLink canonique/transaction Core est livré de cette seule recette.

`kobo-audit --credential CONNEXION --plan` reste un diagnostic en lecture seule.
Ses comptes pseudonymisés et ses `case-*` éphémères ne peuvent jamais servir de
cible d'exécution. `evidence_status` distingue les octets locaux vérifiés de
l'absence de preuve vérifiée ; un fichier non indexé reste un **candidat**, pas
un orphelin Kobo attesté.

`kobo-reconcile-local` restaure **un lien de fichier existant**, explicitement
sélectionné. Il ne nettoie pas les originaux. Il n'active aucun plugin, ne se
connecte à aucun fournisseur, ne purge aucun cache et ne reprend aucune migration.
Un profil et un schéma existants sont requis. Une migration de dossier en attente
doit être terminée ou annulée par son propre parcours avant le rapprochement.

## Aperçu puis confirmation

Choisir manuellement la connexion, le compte, le product ID et le fichier. Donner
son SHA-256 en minuscules : il doit **déjà être attesté dans le backup ledger**.
Calculer le SHA du candidat seul ne crée aucune preuve de droits.

```bash
cifereca-cli kobo-reconcile-local \
  --credential CONNEXION --account-id COMPTE --item PRODUIT \
  --sha256 EMPREINTE --path /bibliotheque/identite/kobo/candidat.epub
```

Sans `--confirm`, connexion SQLite en lecture seule, aucun changement métier ni
fichier de verrou créé. La sortie JSON expurgée contient un `plan_token`, jamais
le chemin, le titre, les identifiants privés ou le SHA du livre. Relancer les
**mêmes arguments** avec `--confirm TOKEN` pour appliquer le rapprochement.
`--identity IDENTITE` permet d'exiger en plus l'identité attendue.

Le token lie la base, la connexion/identité/compte, les droits et la révision,
le ledger, les index locaux, les racines configurées et les octets/stat du
fichier. Toute modification pertinente exige un nouvel aperçu. Un double appel
après succès est sans effet (`already_linked`).

## Conditions et transaction

- Droit local déjà actif, **possédé**, sans échéance ni prêt, même compte et même
  révision attestée. Aucune création, réactivation ou réaffectation de droit.
- SHA et taille du candidat conformes au backup et à l'artefact générique existant.
- EPUB/PDF régulier sous le dossier Kobo de cette identité ou son ancien dossier
  plat **propre à l'identité**. Aucun parcours du dossier global ambigu.
  Un KEPUB déjà attesté, stocké en `.epub`, conserve son format et ses octets.
- Aucun lien symbolique sur le chemin ou ses ancêtres. Pas de rapprochement par
  titre, nom de fichier ou ISBN ; conflit d'identité, produit, artefact ou index
  ambigu refusé. Preuve/cache manquants : refus, jamais de reconstitution supposée.

L'application et l'annulation utilisent le verrou d'acquisition commun, puis
`BEGIN IMMEDIATE` et `synchronous=FULL`. Backup, acquisition ledger, placement,
catalogue et journal sont validés ensemble. Les droits et l'artefact ne changent
pas. Les autres chemins/placements et toutes les copies restent conservés.
Les octets et la configuration sont revérifiés juste avant le commit.

Ce verrou sérialise les écrivains coopératifs de Cifereca ; SQLite ne peut pas
verrouiller une modification arbitraire du fichier par un programme externe.
Une modification détectée ou une erreur SQL annule toute la transaction.

## Annulation confirmée

Conserver l'`operation_id` renvoyé après application. Le journal local en base
contient les états avant/après ; cet identifiant est une adresse de contenu
avec nonce aléatoire. Altérer l'historique invalide l'identifiant conservé.

```bash
cifereca-cli kobo-reconcile-local \
  --credential CONNEXION --account-id COMPTE --item PRODUIT \
  --sha256 EMPREINTE --undo OPERATION
```

Cette commande donne un **nouvel aperçu**. Ajouter `--confirm TOKEN_ANNULATION`
pour restaurer les lignes précédentes. Si un index, une annotation, le compte,
les droits ou les fichiers ont changé, l'annulation refuse d'écraser les nouveaux
états. Répéter une annulation déjà effectuée est sans effet (`already_undone`).
Le journal est conservé ; aucun fichier n'est supprimé, même à l'annulation.

Code de sortie : `0` pour aperçu/succès/sans effet ; `2` pour refus ou erreur.
Les raisons sont des codes fixes expurgés. Ce parcours ne télécharge rien, ne
vérifie pas le DRM et ne prétend pas réparer une édition aux octets divergents.
