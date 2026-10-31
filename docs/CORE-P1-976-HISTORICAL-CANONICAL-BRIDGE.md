# P1 #976 — rapprochement local des provenances Paris/ADEPT

## Périmètre

`review_paris_adept_transaction` rapproche **en lecture seule** deux audits
existants :

- Historique : `inspect_paris_adept_origins` lit les emprunts Paris,
  acquisitions et inventaires ADEPT **déjà persistés**.
- Canonique : `review_paris_canonical_adept_links` lit SourceReference v14 et
  OriginLink `provider`/`adept` par droit et Artifact.

Les deux prennent exactement le même `loan_id` et `item_id`. La seule
correspondance transversale possible est le **scope local Paris complet**
`(identity_id, credential_id, account_id)`, calculé et comparé avec les
références opaques de l'audit historique. Le SHA d'un Artifact, un titre,
un ISBN, une URL ou une identité de rangement seule **ne sont jamais** une
clé de rapprochement. Aucun ancien reçu n'est migré en droit canonique.

## Commande CLI

```bash
cifereca-cli paris-adept audit-transaction \
  --loan-id "<id-pret-paris>" --item-id "<id-item>"
```

La commande exige les deux identifiants exacts, refuse les arguments vides
ou contenant NUL **avant** d'ouvrir Core, et lit exclusivement les données
locales persistées. Elle ne connecte aucun compte Paris ou Adobe et ne
déclenche aucun traitement d'acquisition. Le JSON produit est un **rapport
de revue** ; il n'apporte aucune permission nouvelle.

## Sémantique des résultats

- `scope_overlap_only` : les scopes locaux se recoupent sans conflit de
  branche ou de provenance observé. **Cela ne prouve pas** qu'il s'agit de
  la même transaction Adobe, et encore moins son profil d'activation.
- `evidence_gap` : SourceReference absente, droit historique non disponible,
  acquisition seulement, source canonique non active/état inconnu ou
  OriginLink manquant. Une absence de droit historique ne révoque rien,
  et une source `inactive` n'est pas automatiquement une décision serveur.
- `conflicting_records` : revendications ADEPT contradictoires, liens
  canoniques incompatibles ou plusieurs branches canoniques pour un même
  compte historique sans identifiant de branche correspondant.
- `unknown` : scope d'entrée invalide, refusé **avant** toute lecture.

Chaque SourceReference conserve ses identifiants de compte et de branche,
son éventuelle visibilité dans le droit historique, le nombre d'acquisitions
historiques au même scope et le nombre de paires OriginLink *non attestées*.
Les acquittements de licence/secrets ne sont pas publiés, sans altération du
journal brut.

## Autorité et suite

`adobe_profile_resolution="unknown"`,
`return_authorized=false` et `requires_explicit_review=true` sont
**invariables**. Même le cas le mieux documenté `scope_overlap_only`
ne produit **aucun** grant, décision de retour, activation, déchiffrement,
replay ACSM ou écriture SQLite.

Le passage à un résolveur explicite (#976) exige une preuve transactionnelle
contrôlée de l'**identifiant de prêt Paris + compte + branche** et de la
**licence et du profil Adobe ayant réellement activé**, idéalement appuyée
par l'inventaire ADEPT et le reçu final, sans confondre OriginLink et licence
server-side. Le contrôle de retour #984 est séparé et reste fail-closed.

La sonde et la commande CLI sont des **diagnostics en lecture seule** ;
aucun bouton GUI ou API de mutation n'est créé.
