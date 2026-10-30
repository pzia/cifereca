# Liaison commune des bibliothèques privées — #3719

La Sync explicite utilise `private_inventory_binding_current` avant la lecture
de la bibliothèque privée, après cette lecture et après ses miroirs de
compatibilité. Les coordonnées profil/fournisseur/connexion/compte et l’époque
Core sont épinglées ; chaque contrôle retrouve le compte exact, la session
unique initiale, le profil et la connexion persistée activée. Un changement
de compte, de session ou d’époque ne peut pas projeter des notices sous
l’ancienne identité. Le profil sélectionné dans l’interface n’est pas utilisé.

Le compte amont vide est un identifiant primaire exact, pas un joker. Une
session v1 dépourvue de projection locale `accounts` reste compatible avec
une seule liaison de compte. Plusieurs comptes de la même connexion exigent
une projection explicite contenant exactement une fois le compte demandé.
Une connexion ne s’étend jamais à une autre identité.

Cette vérification ne donne aucun consentement d’acquisition et ne crée ni
droit, ni origine, ni fichier. Les décisions de livraison et de publication
restent dans le moteur canonique et ses propres gardes. Le contrôle plus
conservateur de planification `source_refresh_policy.py` est distinct et
inchangé ; aucune inscription à un horaire n’est créée ici.

Les miroirs de métadonnées et validations des observations historiques sont
conservés, ainsi que les lectures de prêts. Ils ne sont pas remplacés par une
liste de ressources téléchargeables qui perdrait leurs métadonnées. Le retrait
des autres dispatchs et moteurs historiques, et la déduplication physique
après placement, restent suivis dans #3719/#3171.

Tests : `tests/test_private_inventory_binding.py`, plus les suites existantes
Kobo/Numilog, source Sync et lecteurs de prêts. Les tests synthétiques ne
constituent pas une réception des six EPUB Numilog de production.


## Miroir d’ouverture

Le miroir de métadonnées d’ouverture utilise la même vérification avant
lecture, après lecture et après les miroirs historiques. Le contrat v1
`library()` n’ayant pas de paramètre de compte, il exige une seule liaison
enregistrée pour la connexion ; il ne sélectionne ni le premier emprunteur
d’une liste, ni le profil UI. La convergence typée par compte garde son propre
parcours et n’est pas remplacée par cette projection de métadonnées.

Un instantané des sessions borne le lot : une session ajoutée pendant une
lecture ne peut ni interrompre l’itération ni s’inscrire dans ce même lot.
Une route ambiguë est comptée une seule fois. L’annulation se propage comme
telle, sans conversion en échec fournisseur. Les droits descriptifs et
métadonnées historiques sont conservés par leurs adaptateurs existants.

`tests/test_private_inventory_opening_mirror.py` couvre le fournisseur inconnu,
les changements en cours de lecture, l’ambiguïté des comptes/sessions, les
ajouts de sessions, la désactivation et l’annulation. Les suites historiques
de miroir Kobo/Numilog et l’intégration réelle restent à exécuter en CI.
