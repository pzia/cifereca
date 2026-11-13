# Classification des cas pytest par lot

Ajouter un fichier JSON distinct par lot ou plugin. Chaque fichier reprend
le schéma de [l’inventaire principal](../test-classification-manifest.json).
Un chemin de test appartient à un seul manifeste. En cas de doublon,
l’inventaire signale le chemin et les deux manifestes concernés.

Depuis la racine du dépôt, la collecte charge le manifeste principal et
tous les fichiers JSON de ce répertoire. Pour vérifier un seul lot, passer
`--manifest docs/test-classification-manifest.d/<lot>.json` à
`python3 -m scripts.ci_test_collection_inventory`, avec la suite et le chemin
de sortie habituels. Les tests non couverts gardent leur classification
`unclassified`.
