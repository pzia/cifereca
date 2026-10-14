"""Read actual GUI declaration owners through explicitly maintained reexports.

Existing source assertions keep their full bodies. This avoids imposing their
physical placement in MainWindow; it does not synthesize or stub declarations.
"""
import ast
from pathlib import Path

EXPORTS = {
    'master_detail': {'MasterDetailPage'},
    'plugin_ui_host': {'_UiHost'},
    'public_book_view': {'PublicBookMasterDetailView'},
    'detail_model': None,
    'detail_panel': {'DetailPanel'},
    'history_cover_resolver': {'HistoryCoverResolver', '_MetaRequest'},
    'action_widgets': {'clear_box', 'add_css', 'action_button'},
}

DELEGATES = {
    'search_controller': {
        '_reset_search_enter', '_search_controls_changed', '_search_enter',
        '_search_key_interaction', '_build_search_page', 'search_books', 'run_search',
    },
    'search_details': {
        '_search_selected', '_search_detail_request_context',
        '_search_detail_context_current', '_apply_search_detail',
        '_search_detail_error', '_load_known_detail',
    },
    'search_results': {
        '_sync_search_detail_after_results', '_populate_local_search',
        '_populate_search', '_render_search_results', '_update_search_count',
    },
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
    source = ''.join(lines)
    lines = source.splitlines(keepends=True)
    replacements = []
    window = next(n for n in ast.parse(source).body
                  if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    for method in window.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        body = method.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        # A narrow host port adds a local import before the delegation.
        # Keep source-contract tests bound to the real owner implementation.
        if len(body) == 2 and isinstance(body[0], ast.ImportFrom) and body[0].module == 'search_host_port':
            body = body[1:]
        if len(body) != 1:
            continue
        statement = body[0]
        call = statement.value if isinstance(statement, ast.Return) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        module = call.func.value
        if not isinstance(module, ast.Name) or method.name not in DELEGATES.get(module.id, set()):
            continue
        assert call.func.attr == method.name
        assert call.args and (isinstance(call.args[0], ast.Name) and call.args[0].id == 'self' or isinstance(call.args[0], ast.Call) and len(call.args[0].args) == 1 and isinstance(call.args[0].args[0], ast.Name) and call.args[0].args[0].id == 'self' and isinstance(call.args[0].func, ast.Name) and call.args[0].func.id in {'SearchRunPort', 'SearchDetailPort'})
        owner = path.parent / (module.id + '.py')
        owner_text = owner.read_text(encoding='utf-8')
        declaration = next(n for n in ast.parse(owner_text).body
                           if isinstance(n, ast.FunctionDef) and n.name == method.name)
        chunk = owner_text.splitlines(keepends=True)[declaration.lineno - 1:declaration.end_lineno]
        replacements.append((method.lineno - 1, method.end_lineno,
                             ''.join('    ' + line for line in chunk)))
    for first, last, text in sorted(replacements, reverse=True):
        lines[first:last] = [text]
    return ''.join(lines)
