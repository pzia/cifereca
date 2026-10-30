# Adapter les prêts Paris/BnF-Prêt à downloadable-resource/2 — #3723

Depuis le contrat public Core #3730, les deux sessions fournissent :

- `downloadable_resource_snapshot(account)` : observation typée positive (sans GET de livraison) issue de la lecture privative existante.
- `downloadable_selected_offer_snapshot(account, edition_id, resource_id, kind, branch, explicit_user_action=True)` : préflight sélectionné exact ; état de droit purement descriptif.
- `downloadable_selected_delivery_candidate(account, edition_id, resource_id, kind, branch, selected_offer_id, target_dir, explicit_user_action=True)` : récupération d'un candidat en staging **déjà admis** par le Core, jamais preuve de SHA final, d'Artifact, d'OriginLink ou de Placement.

Le chemin `rights_selected_delivery_*` v1 reste en place pour compatibilité et constitue la seule implémentation des gardes de compte, lecteur, credential, action signée et session. Le convertisseur public `from_legacy_rights_snapshot` ne rend pas téléchargeable une assertion legacy non attestée. Le provider ne décide ni consentement ON/OFF, ni rapprochement local, ni déduplication physique.

**BnF-Prêt** : offre ACS4 exacte du prêt HTML courant, token/ACSM et ADEPT après le Core. Les absences de l'inventaire partiel ne valent pas révocation.

**Paris** : prêt DILICOM, action EbookUrl privée fraîche et scope exact du lecteur ; sans second POST d'emprunt/quota ; aucun lien privé transféré à l'interface canonique. L'account_id primaire vide reste admis.

Après fusion de l'ABI Core #3730, les tests `test_p0_3723_downloadable_v2.py` doivent être exécutés *sans skips* sous leurs deux packages. Les suites HTTP synthétiques historiques valident séparément les refus de rebind et la livraison physique réelle. Aucun compte utilisateur de production n'est exploité.
