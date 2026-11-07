# P0 #4858 — activation du compte Kobo ciblé pour les commandes canoniques

## Défaut réel dans la CLI

`connect_kobo_preview(credential)` ouvre uniquement la session Kobo
demandée et ne modifie pas les comptes du Core. Cette propriété demeure
indispensable pour `kobo-resume-preview`.

Mais les commandes confirmées `kobo-resume` (ABI A→B complet) et
`kobo-sync-edition` nécessitent un `Account` inscrit dans
`LibraryService.accounts`. Le lecteur canonique A exige également que
l'identité du compte présent **dans la session fournisseur** corresponde à
celle du Core. Or un nouveau `CiferecaCore.open()` n'a initialement que le
compte local et Kobo Vault construit son `Account` fournisseur avec
`identity_id="default"`. Le seul `connect_kobo_preview` ne suffit donc
pas : une commande confirmée peut refuser tous les droits pourtant
correctement liés dans SQLite, sans même atteindre l'offre A.

## Réparation strictement bornée

La nouvelle fonction `LibraryService.connect_kobo_canonical(credential)`
réutilise `connect_kobo_preview` mais active le compte en mémoire **seulement
pour un ABI A→B complet** et après vérification **indépendante** des données
SQLite déjà persistées :

- unique session Kobo connectée du credential demandé ;
- unique `Account` Kobo fournisseur de même credential et même borrower ;
- profil Core connu, connexion activée et borrower exact dans
  `provider_scope_account_bound` (lecture SQL, **aucune insertion**) ;
- **même propriétaire** dans la configuration locale du credential actif et
  dans SQLite : une réaffectation du compte de A vers B non encore réconciliée
  ne peut réutiliser le consentement historique de A ;
- identité fournisseur préexistante égale au propriétaire Core, ou au seul
  marqueur historique de modèle `default` ; toute identité explicite
  différente est refusée ;
- absence d'un second compte Core de credential identique ou incohérent ;
- relecture de l'objet session/compte juste avant la mutation mémoire.

Le `Account` fournisseur est ensuite normalisé avec l'identité Core
attestée, comme le faisait déjà `connect_configured`, puis enregistré
dans la liste des comptes Core uniquement s'il n'y figure pas déjà.
L'époque Core est avancée pour révoquer toute admission précédente.
Le lecteur A, les offres, le consentement CAS et le publisher B
revalident à nouveau **leur propre** portée avant chaque GET et commit.

Aucun `provider_connection_put` ni `provider_account_put`, aucun
`backup_items`, entitlement, Artifact, OriginLink, Placement, autre
connexion réseau ou scan. Une session ABI partielle conserve
strictement son comportement de compatibilité.

## Commandes

Seules les commandes explicitement confirmées changent de connecteur :

```sh
cifereca kobo-resume --credential <credential> --confirm-downloads
cifereca kobo-sync-edition --credential <credential> --item <edition> --confirm
```

`kobo-resume-preview` continue d'appeler
`connect_kobo_preview` uniquement si `--refresh-manifest` a été demandé,
sans activation de compte ni écriture SQLite. Le nouveau bind ne constitue
ni un droit Kobo, ni une permission de GET à lui seul.

Recette : `tests/test_core_p0_4858_targeted_kobo_account_activation.py`
(vraie SQLite v14, vrai registre LibraryService, session fictive Kobo ABI
complet, lecture A typée, idempotence, aucun writer 0.7, désactivation
SQL, profil étranger, borrower erroné, double compte Core, rebind
pendant l'admission, compatibilité ABI partiel).
