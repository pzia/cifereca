# Paris — plafonds personnels indicatifs (#4065)

Depuis `provider.paris` 1.2.40, **Paramètres → compte Paris** propose
deux champs numériques indépendants pour l'**emprunteur principal** :

- **Numérique (DILICOM)** : `manual_loan_limit_primary_digital` ;
- **Physique (SYRACUSE)** : `manual_loan_limit_primary_physical`.

Valeurs : entier décimal canonique de 0 à 99, **4 par défaut** si absent
de l'ancienne configuration, via la validation générique `AccountOptionField`.
Les deux réglages sont stockés dans les `options` de **cette connexion
et identité**. Ils sont informatifs, sans effet sur l'autorisation de prêt, 
les refus du serveur, l'acquisition, la synchronisation, l'inventaire,
l'expiration naturelle ni la prolongation physique.

**Ne pas interpréter ces champs comme le plafond des comptes liés.**
Une connexion Paris peut fournir un compte principal et plusieurs lecteurs
liés. Les options `*_primary_*` s'appliquent exclusivement au principal :
aucune valeur ni « reste estimé » n'est héritée par les comptes associés.
Pour les lecteurs liés, le paramétrage individuel (par identité, credential,
`unique_id` emprunteur et branche) nécessite un contrat de réglage
multi-emprunteurs et un éditeur générique adaptés : **reliquat ouvert #4065**.
Ne pas fabriquer cet ID à partir du libellé de lecteur ni appeler le
portail pour remplir les paramètres.

Les quotas authentifiés `CurrentFixedQuota`, `MaxFixedQuota`,
`CurrentSimultaneousQuota`, `MaxSimultaneousQuota` restent des **faits
fournisseur distincts**, pas des synonymes des réglages utilisateur.
Les inventaires DILICOM et SYRACUSE ne prouvent pas encore l'exhaustivité
(#4157) : aucun calcul du « nombre restant » à partir des seuls prêts
observés, et aucune addition numérique + physique.

## Recette

Les tests synthétiques `test_p1_4065_paris_primary_advisory_limit.py`
vérifient les deux descripteurs, le défaut, les limites, l'indépendance
des emprunteurs principal/lié lors de l'inventaire et l'absence
d'utilisation des préférences dans les appels réseau simulés. Le contrat
de persistance/validation 0..99 est celui partagé avec BnF (#4172).
Aucun HTTP authentifié ni mutation de prêt n'est requis.
