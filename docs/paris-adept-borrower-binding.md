# Attribution ADEPT des sous-comptes Paris — exploitation P0 #976

La connexion Paris n'est **pas** une preuve que le titulaire de cette connexion est
l'emprunteur Adobe. Un sous-compte rattaché doit être lié explicitement à une
identité Cifereca et à son profil ADEPT stable avant tout fulfillment ACSM.

## 1. Relever les emprunteurs réellement visibles

```bash
cifereca-cli paris-adept accounts
cifereca-cli paris-adept list
```

`accounts` connecte les fournisseurs configurés pour relever les comptes
effectivement exposés (connexion, identifiant de sous-compte, compte principal).
`list` ne contacte aucun fournisseur ; la sortie inclut les identités et profils
ADEPT locaux et les liaisons existantes. L'indicateur
`configuration_consistent` signifie uniquement que la configuration locale
n'a pas changé ; il **ne prouve pas** la propriété Adobe côté serveur.

## 2. Vérifier hors de Cifereca l'emprunteur Adobe

Déterminer **de manière humaine et explicite** à quel utilisateur Adobe
correspond le sous-compte Paris. Vérifier le profil ADEPT déjà associé à
l'identité Cifereca choisie. En cas d'incertitude, **ne pas confirmer** :
Cifereca conserve `attention_required` et n'active pas l'ACSM.

## 3. Confirmer l'association

```bash
cifereca-cli paris-adept bind \
  --credential "<connexion-paris>" \
  --borrower "<identifiant-sous-compte>" \
  --identity "<identité-emprunteur>" \
  --confirm-ownership
```

`--confirm-ownership` est une attestation explicite de l'opérateur, jamais
un choix déduit de l'identité UI ni une vérification distante automatisée.
La commande vérifie qu'il s'agit d'un sous-compte non principal, visible
sous la connexion exacte, et que le profil ADEPT stable de l'identité existe.
Sans ce drapeau, **aucune ouverture de base ni écriture de liaison** n'a lieu.
Ne pas transmettre mots de passe, jetons ou fichiers ACSM dans les arguments.

## 4. Inspecter / révoquer

```bash
cifereca-cli paris-adept list --credential "<connexion-paris>"
cifereca-cli paris-adept unbind \
  --credential "<connexion-paris>" \
  --borrower "<identifiant-sous-compte>"
```

`unbind` est une opération locale et hors réseau : elle empêche de
nouvelles activations ADEPT sous cette liaison. Elle ne supprime pas les
livres, ne restitue pas les emprunts, ne modifie pas les prêts ni les
acquisitions existantes.

Les liaisons sont invalidées si le propriétaire de la connexion ou le profil
ADEPT stable change. L'activation demeure bloquée tant qu'une nouvelle
confirmation explicite n'est pas faite. Les comptes principaux restent
liés à leur connexion prouvée.

## Limites

- Ne pas utiliser cette procédure pour deviner un profil Adobe.
- Une attestation n'apporte pas à elle seule une preuve serveur Adobe.
- Une même transaction de prêt visible sous plusieurs connexions reste
  protégée par les gardes de provenance et les verrous du Core.
- Avant clôture P0 #976 : recette réelle 2 connexions Paris × 2 profils ADEPT,
  sous-comptes rattachés, prêts communs et vérification de restitution.
