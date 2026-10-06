"""Desktop shortcuts, without GTK imports for contract tests."""
SHORTCUTS = (
    ('quit', ('<Primary>q',), 'Ctrl+Q : quitter'),
    ('close', ('<Primary>w',), 'Ctrl+W : fermer la fenêtre principale'),
    ('pages-menu', ('F10', '<Primary>m'), 'F10 / Ctrl+M : ouvrir le menu des pages'),
    ('focus-search', ('<Primary>f',), 'Ctrl+F : placer le curseur dans la recherche ou le filtre'),
    ('settings', ('<Primary>comma',), 'Ctrl+, : ouvrir Paramètres'),
    ('next-page', ('<Primary>Page_Down',), 'Ctrl+Page suivante : page suivante'),
    ('previous-page', ('<Primary>Page_Up',), 'Ctrl+Page précédente : page précédente'),
    ('go-back', ('<Alt>Left',), 'Alt+Gauche : précédent'),
    ('go-forward', ('<Alt>Right',), 'Alt+Droite : suivant'),
    ('shortcuts', ('F1',), 'F1 : afficher les raccourcis'),
)


def adjacent_route(routes, current, direction):
    if not routes:
        return None
    if current not in routes:
        return routes[0]
    return routes[(routes.index(current) + direction) % len(routes)]


def shortcuts_text():
    return '\n'.join(row[2] for row in SHORTCUTS) + '\n\nTab / Maj+Tab : parcourir les contrôles\nEntrée / Espace : activer le contrôle ciblé\nÉchap : fermer un menu/dialogue ou quitter la sélection de la liste\nFlèches : parcourir les entrées du menu'
