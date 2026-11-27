# Consolidation et capitalisation — #8195

Le dernier arbitrage propriétaire dissocie le transfert déterministe à la clôture
d'un P0 et la capitalisation sémantique différée à la demande. Aucun moteur LLM
dans CI/CD, aucun chiffrement du staging. Voir [politique commune](AGENT-DELIVERY-POLICY.md#consolidation-à-la-clôture-dun-p0).

## Qualification et limites

Focus/Backlog atteste le corpus public clos dans le corps du P0, avant fermeture
completed sous pzia, par une seule ligne :

`<!-- backlog-staging-v1: 123,456 ; public -->`

Les nombres désignent les P-1 effectivement associés ; `none` pour un P0 autonome.
Il ne s'agit pas d'un plan de synthèse. Ne jamais inférer les cibles de simples
mentions #n. Les relations natives doivent être compatibles avec l'inventaire.
Aucun autre agent ne modifie le corpus pendant la clôture. Le pilote reste
borné à trois issues, 1 Mio de manifeste ; au-delà, refus sans mutation.

## Staging en clair

Une référence orpheline par P0, `staging/backlog/p0-N`, sans parent ni fusion
dans main ; manifeste JSON canonique : corps et commentaires intégraux,
identifiants, auteurs publics, dates, références, labels, relations natives.
Pas de dump REST, email, secret, capture privée ou copie des pièces jointes.
Le contrôle heuristique de secrets ne garantit pas une détection exhaustive.
La déclaration de publicité est obligatoire. Pas de chiffrement.

La suppression de la ref ne garantit pas la purge physique des objets GitHub,
ni des copies externes. Le gain logique et le gain physique sont distincts.
Le contenu ne doit pas être publié dans les logs, artefacts ou historique main.

## État de livraison

Le workflow déclenche actuellement une prévalidation en lecture seule lors
de la fermeture completed d'un P0 déclaré. Il vérifie l'identité, le corpus,
la stabilité des snapshots et le SHA courant, puis crée, relit et supprime
une référence orpheline dans un dépôt bare local temporaire. Aucun transfert
distant ni nettoyage GitHub n'est activé. L'ancien apply est désactivé dans
le CLI et le workflow ; ses primitives sont conservées provisoirement pour
réutilisation ou retrait après réception. Audit manuel en lecture seule.
Les tests hors ligne sont lancés avant la prévalidation.

## Réception à compléter

Publication distante et relecture indépendante, anti-rejeu durable, gel,
préservation des liens et preuves, contrôle de chaque mutation, arrêt sûr
et nettoyage limité. Ne jamais nettoyer avant transfert distant vérifié.
La capitalisation à la demande par les LLM des rôles propriétaires doit
relire les manifestes, intégrer les connaissances dans les documents/skills
canoniques, réceptionner les changements sur main et autoriser la purge
logique du staging traité. Conserver un journal minimal de réception.
Distinguer clôture fonctionnelle, consolidation, capitalisation, purge et
gain global ; aucun PASS implicite. Recette synthétique puis pilote réel.
