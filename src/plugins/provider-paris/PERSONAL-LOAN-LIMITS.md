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
La version **1.2.41** (Core Plugin API **>=1.4.6**) ajoute un éditeur
générique à partir des lecteurs liés **déjà connus** du Core. Les plafonds
liés sont stockés dans
`options.manual_loan_limits_linked[unique_id].digital|physical`
avec 4 comme défaut et une validation identique 0..99. Ce `unique_id`
doit correspondre à l'emprunteur lié exact de la connexion et de
l'identité choisies, jamais à son nom d'affichage. Une identité étrangère,
une autre connexion et le compte principal `unique_id=""` ne peuvent pas
alimenter ces champs. Les valeurs des lecteurs déjà enregistrés restent
éditables **hors ligne** ; l'ouverture des Paramètres n'effectue aucun GET.
Les valeurs de `*_primary_*` n'ont aucun effet sur les lecteurs liés.

Aucun changement implicite de profil avec des préférences liées existantes
n'est permis : une nouvelle connexion explicite est nécessaire pour changer
leur propriétaire. Les snapshots booléens, négatifs, flottants, champs
malformés ou contradictoires sont rejetés sans effacer les extensions
de configuration.

Les quotas authentifiés `CurrentFixedQuota`, `MaxFixedQuota`,
`CurrentSimultaneousQuota`, `MaxSimultaneousQuota` restent des **faits
fournisseur distincts**, pas des synonymes des réglages utilisateur.
Les inventaires DILICOM et SYRACUSE ne prouvent pas encore l'exhaustivité
(#4157) : aucun calcul du « nombre restant » à partir des seuls prêts
observés, et aucune addition numérique + physique.

## Recette

Les tests synthétiques `test_p1_4065_paris_primary_advisory_limit.py`
et `tests/test_p1_4065_linked_borrower_settings.py`
vérifient les deux descripteurs, le défaut, les limites, l'indépendance
des emprunteurs principal/lié lors de l'inventaire et l'absence
d'utilisation des préférences dans les appels réseau simulés. Le contrat
de persistance/validation 0..99 est celui partagé avec BnF (#4172).
Aucun HTTP authentifié ni mutation de prêt n'est requis.
