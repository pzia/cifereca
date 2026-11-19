# #4520 / #4496 — Proposition de politique de rétention durable

Statut : **contrat proposé, non activé**. Aucun changement des quotas,
aucune compaction ni suppression autorisée par ce document.

## Séparer trois problèmes

| Objet | Autorité | Comptabilité |
|---|---|---|
| Réservation opérationnelle pending | Writer Core, reprise exacte | Quotas actuels ; jamais évincée pour faire place |
| Reçu committed | Preuve durable de publication, pas une autorisation de reprise | Quotas actuels tant que la politique n'est pas approuvée |
| Stock physique staging | Nom, inode et dépendances à qualifier séparément | Ne pas confondre taille logique et espace récupérable |

L'origine canonique n'est pas le journal opérationnel : Artifact, Placement
et OriginLink restent intacts, y compris si la variante a été retirée par
une autre opération légitime. Une preuve de publication n'est jamais un
droit fournisseur. Le réglage maintenance.destructive_cleanup_enabled
(défaut false) n'autorise pas à lui seul une transition de rétention.

## Transition proposée : archivage intégral sans unlink

1. Revue explicite d'un reçu committed exact : clé, SHA du payload original,
   identité, génération de configuration, racine et preuves actuelles.
   Aucun curseur, compteur agrégé, nom ou âge seul ne fait autorité.
2. Empaqueter les octets originaux sans réécriture du JSON ni perte de
   contexte : format versionné, longueur, digest, identité et clé d'origine.
   L'archive reste privée Core, sans promotion en source d'acquisition.
3. Le futur transfert transactionnel SQL, sous verrou interprocessus
   d'acquisition et BEGIN IMMEDIATE/FULL, doit relire et requalifier le
   reçu exact, insérer le témoin immuable (unicité clé/digest) et retirer
   l'entrée opérationnelle seulement si l'insertion est attestée. Un reçu
   pending, un doublon d'archive divergent ou une transaction préexistante
   bloquent. Pas de procédure fondée sur deux stores non atomiques.
4. Après commit, réouvrir/relire les mêmes témoins et vérifier les comptes.
   Une erreur ambiguë ne déclenche ni nouveau transfert ni remboursement
   automatique. La restauration supervisée doit reconstruire exactement
   les octets et la clé, sous les mêmes contrôles de collision/capacité ;
   elle ne doit jamais recréer un pending ni relancer une publication.
5. Les fichiers sont inchangés pendant ce transfert. Toute libération
   éventuelle d'un nom de staging est une seconde opération à preuve
   indépendante, activation explicite et confirmation. Le fichier
   canonique n'est jamais le candidat à cette opération.

## Capacité et arbitrages restant ouverts

Conserver les plafonds opérationnels existants (1 024 reçus, 8 MiB de
payload, 16 GiB logiques ; 8 192 octets par reçu). Définir séparément des
plafonds d'archive, le coût de stockage DB et la politique de sauvegarde.
Le déplacement de payload vers une autre table ne crée pas de capacité
physique et doit rester comptabilisé dans un budget total explicite.
Un SHA seul n'est pas une archive réversible.

L'inventaire actuel ne couvre pas les consommateurs hors racine, fichiers
ouverts, configurations externes, systèmes de sauvegarde externes,
montages/alias non détectables ni les usages non déclarés par plugins.
L'observation de liens ou l'absence de références connues ne confère
aucune autorité. Un garde de maintenance ne doit pas faire croire à
l'exhaustivité. La stratégie maximale à saturation sans autorisation
supplémentaire reste le refus fermé, sans modifier l'historique.

Décisions propriétaire nécessaires avant activation : budget archive et
budget global ; conditions d'admission à l'archivage ; remboursement
éventuel du quota opérationnel ; politique de conservation des témoins ;
éligibilité éventuelle du retrait du staging. Aucun TTL automatique ni
suppression physique supposé. La rétention durable ne peut pas offrir une
capacité indéfinie avec un budget fini et une conservation intégrale.

## Recette minimale avant branchement

- Transfert/restauration exacts des octets, idempotence, collisions,
  receipt pending et profil étranger refusés, aucune modification FS/B.
- Saturation aux limites opérationnelle, archive et globale, sans éviction.
- Échecs injectés aux frontières transactionnelles ; redémarrage et
  relecture ; absence d'autorisation de reprise depuis une archive.
- Révocation, revalidation SQL/FS, concurrence entre processus, aucun
  double remboursement. Tests avec config isolée true uniquement pour
  un futur parcours destructif explicitement autorisé et données synthétiques.
- Pas de route UI/plugin/startup vers un writer avant validation du contrat
  de sécurité ; CI sur HEAD exact et recette de données synthétiques.
