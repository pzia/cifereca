# Admission locale d’un rafraîchissement d’inventaire

`core/source_refresh_policy.py::inventory_source_eligible` contrôle une
liaison privée sans interpréter le nom du fournisseur. Cette fonction ne
contacte pas le fournisseur, n’enregistre pas de consentement et ne publie
ni droit ni fichier.

Les coordonnées profil, fournisseur et connexion doivent être des chaînes
non vides sans NUL. Le compte doit être une chaîne sans NUL ; la valeur vide
désigne un compte primaire exact, jamais un joker. Les valeurs composées
uniquement d’espaces sont invalides. Le compte doit être l’unique objet
actif de la connexion et le profil doit encore exister. La source et l’unique connexion
persistée doivent être activées. Une préférence d’activation corrompue ne
permet pas un lancement automatique. Une seule session connectée doit
correspondre aux mêmes fournisseur et connexion, avec `personal_library`
explicitement vrai. La décision réemploie le garde Core
`private_inventory_binding_current` utilisé par la Sync explicite et le
miroir d’ouverture : même session inscrite sous sa clé canonique, session
non fermée, génération de contexte valide et projection upstream de comptes
non contradictoire. La capacité `backup` et le profil sélectionné dans
l’interface ne participent pas à cette décision.

Les noms `kobo_inventory_source_eligible` et
`numilog_inventory_source_eligible` restent des façades compatibles limitées
à leur ancien fournisseur. Ils ne portent plus d’algorithmes concurrents.
Les anciennes clés de planification et leurs valeurs sont conservées ;
aucun compte n’est inscrit implicitement à une planification.

Cette tranche de #3719 ne généralise pas encore l’exposition des horaires
dans `SynchronizationBrokerImpl`, toujours bornée aux fournisseurs déjà
pris en charge. Elle ne remplace pas les gardes de `sync_coordination.py`,
les adaptations de protocole, le consentement d’acquisition source ni la
déduplication après placement. Le moteur doit toujours réadmettre une
opération à ses frontières ; ce contrôle passif n’est pas un bail.

`tests/test_inventory_source_binding.py` applique les mêmes cas à un
fournisseur inconnu, Kobo, Numilog, Paris et BnF : coordonnées invalides,
profil supprimé, compte détaché/dupliqué, connexion réaffectée/désactivée,
sessions ambiguës, booléens invalides et échec de stockage. Les tests
existants du broker et du stockage restent nécessaires à l’intégration.
