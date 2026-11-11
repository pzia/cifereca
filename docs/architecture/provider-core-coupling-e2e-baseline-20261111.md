# Baseline GTK indépendante — #5048 / #5376

Ce fichier n'affecte aucun module exécutable. Sa PR provisoire, fondée sur
`main` au commit `755144ecb9ab5c2af20fc5b094b63ef0bb4ff3bb`, déclenche les
workflows habituels **PR CI**, **installation/rollback** et **E2E GTK**.

## Question expérimentale

Les PR #5598 (Kobo W2) et #5618 (Numilog CoreUI) ont chacune 38/44
scénarios GTK verts et les **mêmes six** scénarios `book-detail-*`
rouges. Cette PR de documentation sans changement de code sert de témoin
indépendant : elle vérifie si le défaut Search→Library / `verify_library`
existe également sur `main`.

Le résultat ne doit pas être déclaré avant la fin des trois workflows. Les
artefacts exportés contiennent uniquement les codes de scénarios et les
checkpoints publics ; les traces brutes et valeurs bibliographiques privées
ne doivent pas être copiées dans les tickets.

## Suite

- Si le témoin échoue de la même manière : traiter le défaut partagé dans
  un ticket/PR distinct, sans réintroduire les modules fournisseurs retirés.
- Si le témoin passe : investiguer les différences de code de chaque PR.
- Dans les deux cas, ne fusionner les PR #5598/#5618 que sur leurs HEAD
  exacts après gates requis.
- Clore cette PR témoin sans fusion et supprimer sa branche après mesure.
