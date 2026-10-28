# Core P0 #3196 — frontière du prune historique Kobo

La méthode `Storage.backup_prune_missing(provider, credential, seen_item_ids)` peut désactiver l'entitlement miroir d'un livre devenu absent **uniquement** après un inventaire amont complet. Elle reste transitoire : `backup_items` n'est ni un témoin d'octets, ni un OriginLink, ni une autorité de provenance.

## Admission du manifeste

La liste exacte des IDs observés doit être un `set`, une `list` ou un `tuple`, borné à 100 000 entrées, comprenant exclusivement des chaînes d'IDs non vides (au plus 1 024 caractères, sans NUL/CR/LF). Les entrées incorrectes rejettent le **lot entier avant SQL** par `invalid_authoritative_manifest_item_ids`. En particulier `None`, une chaîne isolée, un itérateur, un dictionnaire et un mélange d'IDs valides/invalides ne valent jamais attestation d'inventaire vide.

Une collection vide **valide** reste un inventaire vide potentiellement complet et conserve la sémantique existante de prune **après** admission par le synchroniseur Kobo ; cette méthode ne peut pas elle-même certifier la complétude fournisseur. Les droits d'un autre compte/identité et les Placements restent inchangés.

## Recette

`tests/test_core_p0_3196_prune_exact_right_scope.py` exerce les entrées invalides, les trois types autorisés, le comportement vide explicite, les refus d'usurpation d'identité et la conservation de l'historique. La CI Core/Plugins fournit la preuve de régression ; aucune authentification réelle et aucune migration destructrice de `backup_items` n'est revendiquée.

## Non couvert

Retrait des writers `backup_put`, des lecteurs Kobo hérités, migration 0.7/0.8, publication des reçus live et reprise crash : **#3196 reste ouvert**. Aucun travail Plugins #3252 ni P2.
