# P0 #3196 / W1 — réconciliation Kobo déconnectée en archive seule

## Constat après retrait W4 (#4924, #4928)

Une ligne `backup_items` en `reconcile_required` ou
`reconcile_conflict` est un **checkpoint 0.7**, pas un droit fournisseur,
une preuve d'acquisition ni un fichier B. Son `sha256`, sa taille et son
`local_path` sont de simples déclarations historiques, même avec une
empreinte syntaxiquement valide.

Avant cette tranche, sans connexion Kobo exacte et activée, la présence
simultanée d'une empreinte SHA-256 et d'une taille positive suffisait encore à
autoriser la projection W1 de `entitlements`, `acquisition_ledger`,
`artifacts`, `placements` et `catalog_entries`. L'ancien rapprochement W4,
désormais retiré des façades, ne justifie plus cette exception.

## Contrat transactionnel

`Storage.backup_put()` continue à archiver le reçu 0.7 sous son identité et
son emprunteur enregistrés. Pour les deux statuts de rapprochement, le miroir
0.8 n'est accessible que si la transaction voit une connexion
`provider_connections` **exactement** égale à
`(provider=kobo, credential, identity)` et `enabled=1`.

Même sans cette connexion, `_check_kobo_legacy_canonical_owner` reste exécuté
pour refuser tout conflit de propriétaire ou de scope avec un droit/reçu déjà
persisté. Une désactivation ou un autre propriétaire ne transforme pas les
octets historiques en droits et n'efface aucune ligne canonique préexistante.

Les comptes Kobo actifs avec liaison exacte conservent *provisoirement* le
miroir de découverte W2, y compris sans preuve physique, tant que leur inventaire
OWN n'a pas de publication typée complète. Cette compatibilité **n'atteste pas
de fichier B**, et n'autorise pas un GET supplémentaire ni une annulation W4.

## Recette

`tests/test_core_p0_3196_w1_passive_kobo_checkpoint_cutover.py` couvre les
deux statuts, une empreinte et une taille valides, les connexions absentes,
activées pour le bon/mauvais propriétaire, et désactivées ; replays
idempotents et absence d'Artifact, Placement, ledger, catalogue ou
entitlement nouvellement créés lorsque le binding n'est pas actif.

**Non-périmètre :** les autres statuts W1, la livraison privée W2,
le renouvellement des droits A, une migration des archives et le retrait du
miroir pour les sessions Kobo actives. #3196 reste ouvert jusqu'au cutover
global W1/W2/D1–D4 validé.
