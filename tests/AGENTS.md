# Instructions agents — Tests centraux

Hérite de `/AGENTS.md`.

Classer le test selon l'invariant affirmé :

- invariant Core ou contrat transversal : rôle propriétaire `core-core` ou `core-ui` ;
- parcours traversant Core/UI/plugins : rôle **`e2e`** ;
- assertion propre à un plugin : déplacer le test dans `src/plugins/<plugin>/tests/` et utiliser `plugin-cible`.

Le rôle `e2e` ne modifie pas silencieusement le produit pour faire passer un scénario : il documente le défaut et le transmet au rôle propriétaire.

Profil : `/.agents/roles/e2e.md`.
