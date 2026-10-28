# Core P0 #3171 — terminer une livraison candidate BnF/Numilog

Dans la **Sync source unique**, après observation A positive, consentement
persistant exact et préflight ciblé #3608, Core peut maintenant consommer la
facette optionnelle des sessions fournisseurs #3606 :

`rights_selected_delivery_candidate(account, edition_id, right_id, kind,
branch, delivery_offer_id, target_dir, explicit_user_action=True)`

La facette reçoit le `SourceKey` et l'offre opaque autorisés. Elle ne délivre
qu'un couple `(path, source_url)`, sans pouvoir fournir une preuve de livre
final. La Sync ne l'appelle que sous le verrou acquisition, avec les mêmes
contrôles de source, de compte, de consentement durable et de session que pour
l'exécuteur canonique. Les sessions sans cette API restent en lecture seule.

## Réception post-GET Core

- Espace de staging **nouveau et privé** sous le répertoire du profil
  enregistré. Un chemin étranger, symlink, copie préexistante, candidat vide ou
  résultat non typé est refusé ; l'URL privée n'est ni utilisée comme
  signature ni sauvegardée dans SQLite. La projection catalogue ordinaire
  exclut les fichiers du staging **avant** la transaction canonique et ne les
  indexe qu'après un Placement présent et un OriginLink provider attesté.
  Ainsi un GET achevé mais non publié ne crée pas de livre visible.
- EPUB/PDF : inspecter les octets finaux, valider le format, refuser la
  protection manifeste, contrôler inode/mtime/ctime, puis SHA-256 final.
- ACSM : ce fichier n'est jamais publié comme Artifact. Le runtime ADEPT
  configuré avec le profil de l'identité emprunteuse doit confirmer son
  exécution et écrire exactement le fichier EPUB/PDF final dans le staging
  choisi par Core. Une erreur ou une destination divergente bloque B.
- Une opération validée génère un reçu interne `core-delivery:` distinct de
  l'observation A et du hash d'offre. **Ce reçu atteste le callback de
  livraison effectivement exécuté et les octets validés par Core ; il ne
  prétend pas être un accusé de réception émis par le fournisseur.** Après
  ADEPT confirmé, une preuve distincte `core-adept:` peut être liée.
- L'exécuteur canonique déjà en production contrôle encore le consentement
  après réseau, journalise le résultat achevé pour reprise, publie
  `Artifact + SourceReference + Placement + OriginLink(provider/adept)`
  atomiquement et vérifie une deuxième Sync sans GET si les octets restent
  attestés.

## Limites

L'absence de profil ADEPT ou une erreur du runtime après la récupération
d'un token ACSM laisse les octets de staging privés pour résolution ; aucune
publication canonique n'est tentée. Les adaptateurs/credentials/URL sont
fournis uniquement par les plugins existants et ne sont jamais interprétés
dans Core par nom de fournisseur. Les récépissés internes et tests synthétiques
ne remplacent pas la recette live avec deux fournisseurs, les effets DRM
réels, la conservation des obligations de prêt, les relances sous crash ni
la migration complète `backup_items` (#3196). Ne fermer ni #3171 ni #3252
sur cette seule tranche.

Tests : `tests/test_core_p0_3171_selected_candidate_finalization.py`,
plus suites Core/Plugins installées et tests de reprise canonique.
