# #2845 — Lecture de capacité ADEPT dans un lot de rapprochement

## Mesure terrain de référence, 29 octobre 2026 seulement

L'export journal-brut4 contient 22 événements terminaux runtime ADEPT distincts
pour le 29/10, après dédoublonnage par event_id (Europe/Paris). Les débuts et
réponses ne sont pas recomptés. Durées effectivement émises, sans estimation :

| Commande | Catégorie appelante | Appels | Portées opaques distinctes | Somme ms |
| --- | --- | ---: | ---: | ---: |
| contract | activation | 1 | 1 | 213 |
| capabilities | activation | 1 | 1 | 194 |
| profile list | unattributed | 9 | 1 | 2321 |
| loans list | unattributed | 3 | 2 | 801 |
| capabilities | unattributed | 4 | 1 | 1038 |
| contract | core_probe | 2 | 1 | 358 |
| capabilities | core_probe | 2 | 1 | 443 |

Ces agrégats ne prouvent pas l'équivalence des consommateurs ou des sessions.
Le libellé historique « onze appels » n'est pas le comptage de ce lancement.
Aucun profil, credential ou journal brut n'est publié ici.

## Changement Core borné

`_reconcile_identity_adept_inventory` possède déjà un cache local de profils
et d'inventaires, détruit après chaque lot. Le rapprochement relisait encore
`capabilities.adept.loan_return` pour chaque prêt actif résolu. Il réutilise
maintenant seulement une réponse positive de la même instance runtime dans
ce cache local. Une instance remplacée, une erreur ou un résultat négatif ne
bénéficie pas de cette réutilisation. Une nouvelle Sync/recette indépendante
relit les capacités ; aucune durée de vie globale ni mise en cache des droits,
licences, offres ou identifiants d'emprunteur n'est ajoutée.

Le booléen renseigne la disponibilité d'une action ; il n'autorise aucun
retour. Les gardes fraîches du vrai parcours de retour restent inchangées,
notamment capabilities, identité d'origine et inventaire avant/après mutation.
Aucun changement dans les plugins ni dans le contrat runtime 1.3.

## Avant / après reproductible, fournisseurs synthétiques

`test_batch_capabilities_before_after_counts_and_identical_bindings` exerce
le même lot réel LibraryService/SQLite de trois prêts et trois fichiers, via
la façade de capacité réelle. Le mode avant substitue uniquement l'ancienne
lecture inconditionnelle ; le mode après exerce le code livré.

| Lecture | Avant | Après |
| --- | ---: | ---: |
| profile list (façade) | 1 | 1 |
| loan_inventories (façade) | 1 | 1 |
| capabilities | 3 | 1 |

Liaisons identiques (hors heure de vérification), trois rapprochements résolus,
octets inchangés ; seconde exécution : une nouvelle lecture de chaque type.
Tests supplémentaires : remplacement runtime, échec transitoire non mémorisé,
capacité fraîche devenue négative. Pas de gain de temps terrain revendiqué.

#2845 reste ouvert : attribution des autres lectures de profils/prêts,
activation versus core_probe, réductions supplémentaires après preuve de
portée équivalente, et comparaison sur un nouveau démarrage réel.
