# #3275 — Acceptation native de l'action Envoyer

Le scénario Maintenance existant exerce désormais à 1366 et 1600 pixels
l'action de production `add_local_book_actions` dans une vraie fenêtre GTK.
Le bouton utilise la fabrique MainWindow ; XTEST effectue le clic après
vérification de sa cible visible. Le vrai TaskRunner exécute le worker, qui
appelle le vrai `LibraryService.materialize_to_identity` sur SQLite/fichiers.

La fixture isolée contient un PDF synthétique Alice et deux origines source
fournisseur/ADEPT simulées. Aucun appel fournisseur, ADEPT, device ou compte
réel n'est effectué. Avant clic, seul le Placement Alice existe. Après retour
du worker et affichage « Livre envoyé », la recette vérifie :

- copie Bob physiquement indépendante et octets identiques ;
- marqueur d'origine Alice conservé ;
- deux OriginLinks Bob `transfer` et `cifereca` ;
- uniquement une référence Bob interne non synchronisable, aucun droit reçu.

Le rapport porte `P1_3275_native_send_taskrunner_copy_origins=true` seulement
après ces assertions. Les captures `08-native-send-before` et
`09-native-send-after` montrent la vraie fenêtre, sans reconstruction.
Les quatre autres géométries Maintenance conservent leur parcours antérieur.
Le sélecteur existant route ce fichier vers les six scénarios Maintenance ;
aucun workflow ou scénario supplémentaire n'est créé.

Validation locale : compilation Python, fixture Core hors GTK et tests du
harnais/routage. Le verdict natif exige le gate E2E Smoke sur le HEAD de la PR ;
il ne se déduit pas de ces précontrôles. L'acceptation A→B→C/huit origines et
les interruptions de processus restent couvertes séparément par les tests
Core de #3693 et de la reprise pré-commit #3275.
