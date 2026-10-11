"""Read actual GUI declaration owners through explicitly maintained reexports.

Existing source assertions keep their full bodies. This avoids imposing their
physical placement in MainWindow; it does not synthesize or stub declarations.
"""
import ast
from pathlib import Path

EXPORTS = {
    'detail_model': None,
    'detail_panel': {'DetailPanel'},
    'history_cover_resolver': {'HistoryCoverResolver', '_MetaRequest'},
    'action_widgets': {'clear_box', 'add_css', 'action_button'},
}


def gui_source_text(path: Path) -> str:
    path = Path(path)
    source = path.read_text(encoding='utf-8')
    if path.name != 'main_window.py' or path.parent.name != 'gui':
        return source
    lines = source.splitlines(keepends=True)
    replacements = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ImportFrom) or node.level != 1 or node.module not in EXPORTS:
            continue
        allowed = EXPORTS[node.module]
        names = [alias.name for alias in node.names if allowed is None or alias.name in allowed]
        if not names:
            continue
        owner = path.parent / (node.module + '.py')
        text = owner.read_text(encoding='utf-8'); owner_lines = text.splitlines(keepends=True)
        declarations = {n.name: n for n in ast.parse(text).body
                        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
        chunks = []
        for name in names:
            declaration = declarations[name]
            first = min([declaration.lineno, *[d.lineno for d in declaration.decorator_list]]) - 1
            chunks.append(''.join(owner_lines[first:declaration.end_lineno]))
        replacements.append((node.lineno - 1, node.end_lineno, '\n\n'.join(chunks)))
    for first, last, text in sorted(replacements, reverse=True):
        lines[first:last] = [text + '\n']
    return ''.join(lines)
