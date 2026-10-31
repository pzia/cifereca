# Liaisons ADEPT exceptionnelles des comptes Paris — P1 #976

Le **compte emprunteur Paris** (origine du prêt et de l'ACSM), le **profil
ADEPT réellement utilisé** (licence et restitution) et l'**identité de
rangement Cifereca** sont trois provenances distinctes. Ni le nom du compte
Paris, ni l'identité sélectionnée dans l'interface, ni l'import historique
d'un livre ne prouvent quel profil Adobe a activé le prêt.

Pour une *nouvelle* activation, le parcours normal utilise le profil stable
configuré pour l'identité ou une exception explicite, vérifiée et révocable.
Pour une *licence historique*, la liaison actuelle n'est jamais substituée
au profil enregistré lors du fulfillment : l'audit examine les preuves
existantes sans relancer ACSM, sans modifier de binding et sans restituer.

Le compte principal suit normalement le profil associé à son identité (#1063).
Une exception attestée peut aussi lier ce compte au profil d'une autre identité :
utiliser `--borrower primary` dans les commandes `bind` et `unbind` ci-dessous.
`accounts` affiche cette référence canonique même si Paris expose un identifiant
principal vide. Cette exception reste limitée à la connexion Paris désignée et
ne change ni le profil normal de l'identité ni sa bibliothèque de rangement.
Elle est invalidée si le propriétaire de connexion ou le profil attesté change.
La révoquer rétablit la configuration normale pour les nouvelles opérations ;
cela ne modifie aucune acquisition historique et ne déclenche aucun fulfillment.

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

## 2. Vérifier séparément les provenances avant une nouvelle activation

Relever le **compte Paris** ayant obtenu le droit, puis vérifier quel
**profil ADEPT** est autorisé et configuré pour traiter cet ACSM. Ils
peuvent appartenir à des identités Cifereca différentes, y compris pour
un compte Paris principal : ne jamais inférer leur égalité d'un nom ou
d'une connexion. Le profil ADEPT effectif d'une activation *passée*
doit être établi par la provenance enregistrée et, si possible,
l'inventaire existant, **sans activation d'essai**.

En cas d'incertitude pour une opération nouvelle, **ne pas confirmer** :
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
La commande vérifie que le compte ciblé est visible sous la connexion
exacte (sous-compte ou alias canonique `primary`) et que le profil ADEPT
stable de l'identité choisie existe. Elle ne certifie pas une autorisation
Adobe distante.
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
confirmation explicite n'est pas faite. Un compte Paris principal peut aussi recevoir une exception explicite et
révocable : cela ne déplace pas l'origine du prêt ni son droit fournisseur.

## 5. Auditer les emprunts visibles sur plusieurs connexions (hors réseau)

```bash
cifereca-cli paris-adept audit
cifereca-cli paris-adept audit --credential "<connexion-paris>"
```

Pour examiner **une transaction historique précise** par les identifiants
exacts du prêt Paris et de l'item (sans rapprochement par titre, ISBN ou SHA) :

```bash
cifereca-cli paris-adept audit-origin \
  --loan-id "<id-pret-paris>" --item-id "<id-item>"
```

Les identifiants de prêt, d'item et de droit historique doivent être de
**véritables chaînes de caractères** : un nombre, booléen, liste ou objet
JSON n'est jamais converti en texte pour fabriquer une égalité de
transaction. Une métadonnée `provider_loan_id` présente mais malformée
ne bénéficie pas du repli historique par `entitlement_id`.

`audit-origin` reprend le résolveur Core `inspect_paris_adept_origins` :
`sources` = visibilités issues des droits Paris persistés,
`origins` = acquisitions ADEPT historiquement enregistrées (y compris dans
une identité de bibliothèque différente), `evidence` = indice enregistré ou
état d'inventaire précédemment attesté, `origin_resolution` = cohérence des
revendications. Une observation `inventory_attested` **n'est jamais** une
interrogation Adobe actuelle, ni une autorisation de retour. La commande ne
contacte aucun fournisseur et ne modifie ni les fichiers ni la base.

Pour examiner les références canoniques v14 correspondant au **même prêt et item**,
sans confondre SourceReference Paris et OriginLink ADEPT :

```bash
cifereca-cli paris-adept audit-canonical \
  --loan-id "<id-pret-paris>" --item-id "<id-item>"
```

`audit-canonical` distingue les liens `provider` et `adept` persistés pour
**chaque** SourceReference exacte, même lorsque deux comptes partagent un SHA.
`paired_links_unverified` signifie seulement que les deux liens existent en
base, **pas** que le profil Adobe ou son état serveur est attesté. Aucun
rattachement de licence par identité de rangement ou SHA, aucun droit à un
retour : `return_authorized=false`. Les références de reçus ne sont pas
affichées. La sonde est décrite dans
[la revue canonique](CORE-P1-976-CANONICAL-ADEPT-LINK-REVIEW.md).

Pour comparer **les preuves historiques et canoniques** sur le même prêt,
sans aucune inférence de licence à partir du seul SHA :

```bash
cifereca-cli paris-adept audit-transaction \
  --loan-id "<id-pret-paris>" --item-id "<id-item>"
```

`scope_overlap_only` signifie exclusivement un recouvrement de compte et
d'identité Paris en base, pas une preuve d'activation Adobe ; un ancien prêt
sans branche canonique ne peut pas résoudre deux branches du même compte.
`return_authorized=false` reste invariable. Voir
[la revue historique/canonique](CORE-P1-976-HISTORICAL-CANONICAL-BRIDGE.md).

La commande globale `audit` effectue un rapprochement **en lecture seule** des prêts actifs
et acquisitions déjà enregistrés en base, des connexions Paris et des profils
ADEPT associés. Aucune activation, connexion fournisseur, vérification
distante, suppression ou restitution n'est effectuée.

La sortie JSON signale `multiple_connections`, `profile_disagreement`,
`binding_state`, `recorded_adept_profile_id` et `multiple_item_ids`.
Chaque prêt porte aussi `review_reasons` (motifs normalisés) et
`next_read_only_review` (prochaine **vérification humaine en lecture seule**),
par exemple : examiner les comptes Paris lorsque la liaison est incertaine,
rapprocher les transactions lorsque des profils/licences Adobe historiques
se contredisent, ou contrôler les inventaires Adobe déjà disponibles.
`none` signifie uniquement qu'aucune anomalie couverte par cette matrice
n'est détectée, **jamais** qu'un retour ou un fulfillment est autorisé.
Les tentatives ADEPT non terminées ou refusées restent visibles dans les
statuts des acquisitions, mais elles ne constituent pas une preuve de profil
historique activé.
Une livraison terminée par un exécuteur non Adobe ou par une stratégie autre
que `loan_auto` ne constitue pas une activation ADEPT, même en présence d'un
champ historique `adept_binding`. Un ancien reçu sans exécuteur explicite reste
une preuve `recorded` diagnostique, jamais une attestation serveur.
Les identifiants de prêts historiques non textuels sont ignorés, sans
projection de leur contenu potentiellement sensible dans le JSON. Un statut de reçu inconnu est affiché `unknown`,
jamais comme un message arbitraire pouvant contenir des données privées.
Le résolveur Core par transaction (`audit_paris_adept_loan_origins`) est
**distinct de cette commande globale** : il qualifie séparément
`status` (force de la meilleure preuve individuelle),
`origin_resolution` (cohérence de l'ensemble) et les indices d'artefact.
Un `status=inventory_attested` ne transforme jamais deux profils ou
licences contradictoires en origine unique autorisée pour un retour.
Une même référence de licence Adobe avec deux états **attestés dans les
inventaires historiques** (par exemple `active` et `returned`) produit
`adobe_loan_state_disagreement=true` et `origin_resolution=conflicting_records`.
Les indicateurs historiques ne sont ni une requête serveur en temps réel ni
une preuve de restitution Paris : aucune opération Adobe n'est déclenchée.
Un filtre de connexion conserve les autres provenances du même identifiant
de prêt afin de ne pas masquer les conflits. La présence d'un même identifiant
de prêt chez deux connexions ne prouve **pas** à elle seule qu'il s'agit d'une
seule transaction Adobe ; un rapprochement manuel est obligatoire.
`binding_state=locally_consistent` **n'est pas** une preuve côté Adobe.

Si l'inventaire est vide, synchroniser d'abord les sources via les
mécanismes existants : `audit` ne déclenche pas de synchronisation.

## Limites

- Ne pas utiliser cette procédure pour deviner un profil Adobe.
- Une attestation n'apporte pas à elle seule une preuve serveur Adobe.
- Une même transaction de prêt visible sous plusieurs connexions reste
  protégée par les gardes de provenance et les verrous du Core.
- Avant clôture P1 #976 : recette réelle 2 connexions Paris × 2 profils ADEPT,
  sous-comptes rattachés, prêts communs et vérification de restitution
  **explicite** par le profil historique de la licence, jamais par déduction
  depuis le compte emprunteur.
