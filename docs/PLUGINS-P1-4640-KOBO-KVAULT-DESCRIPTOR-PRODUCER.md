# #4640 — Preuve du normaliseur Kobo Vault sur le POST ciblé

## Motif

Le cas K2 observé en production est `library_sync + KDRM + media_format=unknown + source_format=flepub`, donc refuse correctement l'offre. Le Worker KVault de référence transforme pourtant `FLEPUB` en `media_format=epub`. Un `/api/v2/status` lu à la connexion indique une release, **pas nécessairement le code qui a servi un POST ultérieur**.

## Contrat additif côté KVault

Dans la **même** réponse de `POST /api/v2/library/{product_id}/acquisition`, le Worker récent peut renvoyer :

```json
"producer_contract": {
  "release": "0.9.8",
  "contract_revision": "2.3.4",
  "flepub_media_probe": "epub"
}
```

- `release` : constante **compilée** `APP_RELEASE`, distincte d'une variable d'environnement qui peut être trompeuse.
- `flepub_media_probe` : résultat du **normaliseur exécuté à l'instant de cette requête**, sur le littéral `FLEPUB`. Il ne porte aucune donnée de l'utilisateur.
- `contract_revision` : révision déclarée par ce Worker.
- Ces déclarations ne prouvent **ni un SHA source**, ni l'absence d'un proxy intermédiaire ; ne pas les traiter comme une attestation cryptographique.

Cifereca émet seulement `candidate_kvault_flepub_producer_probe=absent|epub|unknown|other`, un `release` numérique borné ou `unavailable`, et une révision numérique bornée ou `unavailable`. Une ancienne réponse sans le champ reste rétrocompatible, sans diagnostic de version inventé.

## Lecture K2

| Cas | Inférence limitée |
| --- | --- |
| `producer_probe=absent` | Worker ancien ou champ non exposé/intermédiaire ; version réelle non déterminée. |
| `producer_probe=epub`, candidat `media_unknown` | **Contradiction dans la même réponse** : examiner le mapping/assemblage du descripteur ou une différence entre normalisation de test et chemin du candidat. |
| `producer_probe=unknown`, candidat `media_unknown` | Normaliseur exécuté incompatible avec le contrat de référence ; examiner version/source déployée. |
| `producer_probe=epub`, candidat `normalized_epub` | Contrat de format cohérent ; n'atteste ni droit ni exécution DRM. |

Aucune nouvelle requête n'est émise par Cifereca, aucun `GET` du fichier, aucun choix d'offre et aucune promotion `unknown → epub`. K1 (2/2 ambigu) et K3 (2/0 refus) continuent à dépendre de leurs diagnostics propres et de la qualification ciblée.

## Recette

Tests synthétiques Cifereca `test_p1_4640_kvault_producer_probe.py` et test intégration KVault `api-v097-integration.test.mjs`. Après déploiement **confirmé** de la nouvelle version Worker et de Cifereca, une seule observation ciblée par référence fournit les catégories. Le ZIP du 06/11 reste recevable comme preuve historique ; il ne saurait contenir rétrospectivement ce champ.
