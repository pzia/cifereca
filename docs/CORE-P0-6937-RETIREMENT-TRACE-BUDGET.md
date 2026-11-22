# #6937 — Justification du budget de traces terminales

## Frontière et unités

- **Journal de publication** : limites normatives historiques inchangées (1 024 reçus, 8 192 octets par reçu, 8 Mio de données de reçus, 16 Gio de sorties réservées). Aucune trace de retrait ne rend de capacité à ce journal.
- **Témoins de retrait privé v1** : espace SQLite distinct sous `derived.publication.retirement.v1:`, un témoin maximum par reçu ; capacité conservatrice de **2 048 octets par témoin × 1 024 = 2 Mio**. Ces deux budgets ne mesurent ni l'occupation disque ni les octets récupérables.

## Justification technique

La trace est sérialisée en JSON canonique, sans chemin utilisateur ni contenu EPUB/PDF. Les seules valeurs variables sont les empreintes hexadécimales (64 caractères chacune), le nonce (32 caractères), les cinq estampilles de `before` et `after`, les deux estampilles de répertoire `region` et `destination`, l'identifiant de montage et le motif terminal. Les entiers autorisés sont bornés à `2**63 - 1` ; les motifs terminaux appartiennent à deux littéraux fixes.

La recette `test_terminal_trace_upper_bound_uses_full_int64_fields` construit et décode les états `prepared` et `retired` avec **toutes les composantes numériques à leur maximum accepté**, mesure la taille canonique complète et vérifie qu'elle reste dans la réservation de 2 048 octets. Cela établit la suffisance de la borne pour ce schéma fixe, plutôt que d'extrapoler depuis la taille d'une fixture courte. Tout changement de schéma doit réévaluer cette mesure et le budget avant acceptation.

À la préparation, `_trace_snapshot` compte déjà chaque trace `prepared` pour sa capacité terminale complète, avant tout `unlink`. Au retrait, une nouvelle validation du budget et des reçus est imposée ; saturations, corruption, modification de témoin et concurrence provoquent un refus, pas une suppression ou une restitution de quota. Les recettes existantes `test_prepared_trace_reserves_full_terminal_capacity` et `test_one_retirement_wins_and_other_process_cannot_replay` attestent respectivement la saturation et le CAS interprocessus.

## Réserve de réception

L'**exclusivité des consommateurs externes** n'est pas établie par ces mesures. `_exclusive_consumer_fence_current` reste inconditionnellement faux en production. Aucun retrait réel, dégel, rotation de copie ni lancement fournisseur n'est autorisé par ce document.
