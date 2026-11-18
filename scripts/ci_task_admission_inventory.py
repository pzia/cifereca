"""#5833: deterministic source-only inventory of task and batch boundaries.

Conservative AST evidence, not a proof of runtime reachability or authority.
Every direct .submit/submit_batch/enqueue, plus references and static getattr
aliases, is enumerated in tracked Core and plugin Python sources. Unknown
receivers/workers remain explicit. Never execute installed plugins/fixtures.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = frozenset({"submit", "_submit", "submit_batch", "enqueue", "enqueue_batch"})
NAVIGATION = frozenset({
    "current_account", "current_identity", "_active_session_key",
    "_account_context_epoch", "_account_context_generation",
    "_identity_context_generation", "get_active", "get_selected",
    "get_visible_child_name", "get_root", "get_text", "_key",
    "session_active", "active_session",
})
GUARD_HINTS = frozenset({
    "valid", "current", "still_current", "action_current", "scope_current",
    "worker_scope_current", "context_current", "cancelled", "check_cancelled",
})


def _chain(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _chain(node.value) + "." + node.attr
    return "<expression>"


class _Scope(ast.NodeVisitor):
    """Collect lexical definitions and assignments, without entering functions."""
    def __init__(self, body):
        self.bindings = {}
        for statement in body:
            self.visit(statement)

    def _add(self, name, node):
        self.bindings.setdefault(name, []).append(node)

    def visit_FunctionDef(self, node):
        self._add(node.name, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._add(node.name, node)

    def visit_Lambda(self, node):
        pass

    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._add(target.id, node.value)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node):
        if node.value is not None and isinstance(node.target, ast.Name):
            self._add(node.target.id, node.value)


class _Inventory(ast.NodeVisitor):
    def __init__(self, path, root):
        self.path, self.rows = path, []
        self.scopes = [("<module>", _Scope(root.body))]
        self.called = set()

    def _resolve(self, node, seen=frozenset()):
        if isinstance(node, ast.Name):
            for _name, scope in reversed(self.scopes):
                defs = scope.bindings.get(node.id)
                if not defs:
                    continue
                # Multiple control-flow assignments are not a proof.
                if len(defs) != 1 or node.id in seen:
                    return None
                return self._resolve(defs[0], seen | {node.id})
            return None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return node
        return None

    def _row(self, node, kind, receiver, method, worker=None):
        resolved = self._resolve(worker)
        attributes, hints = set(), set()
        if resolved:
            for child in ast.walk(resolved):
                if isinstance(child, ast.Attribute) and child.attr in NAVIGATION:
                    attributes.add(child.attr)
                if isinstance(child, ast.Call):
                    name = _chain(child.func).rsplit(".", 1)[-1]
                    if name in GUARD_HINTS:
                        hints.add(name)
        self.rows.append({
            "path": self.path, "line": node.lineno,
            "owner": ".".join(name for name, _ in self.scopes),
            "kind": kind, "receiver": receiver, "method": method,
            "worker_line": resolved.lineno if resolved else None,
            "worker_resolution": "lexical" if resolved else "unresolved",
            "navigation_signals": sorted(attributes),
            "guard_calls_to_review": sorted(hints),
        })

    def visit_Call(self, node):
        method = (node.func.attr if isinstance(node.func, ast.Attribute)
                  else node.func.id if isinstance(node.func, ast.Name) else "")
        if method in SUBMIT:
            self.called.add(id(node.func))
            receiver = _chain(node.func.value) if isinstance(node.func, ast.Attribute) else "<name>"
            # TaskRunner.submit(label, fn); TaskBroker/Executor.submit(fn).
            # No receiver-type inference for submit_batch: keep unresolved.
            worker = next((kw.value for kw in node.keywords
                           if kw.arg in {"fn", "operation", "work", "worker"}), None)
            if worker is None and method == "submit":
                position = 1 if receiver.rsplit(".", 1)[-1] == "runner" else 0
                if len(node.args) > position:
                    worker = node.args[position]
            self._row(node, "call", receiver, method, worker)
        if (method == "getattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in SUBMIT):
            self._row(node, "dynamic_reference", _chain(node.args[0]), node.args[1].value)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in SUBMIT and id(node) not in self.called:
            self._row(node, "reference", _chain(node.value), node.attr)
        self.generic_visit(node)

    def _enter(self, node):
        self.scopes.append((node.name, _Scope(node.body)))
        self.generic_visit(node)
        self.scopes.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter
    visit_ClassDef = _enter


def inventory_source(source: str, path: str):
    root = ast.parse(source, filename=path)
    visitor = _Inventory(path, root)
    visitor.visit(root)
    return sorted(visitor.rows, key=lambda row: (row["line"], row["kind"], row["receiver"]))


def tracked_inventory(root: Path = ROOT):
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "src/cifereca", "src/plugins"], cwd=root,
    ).decode("utf-8").split("\0")
    rows, count = [], 0
    for path in sorted(set(paths)):
        if not path.endswith(".py") or "tests" in Path(path).parts:
            continue
        candidate = root / path
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("task_inventory_missing_or_symlinked_file")
        rows.extend(inventory_source(candidate.read_text(encoding="utf-8"), path))
        count += 1
    if count == 0 or not rows:
        raise ValueError("task_inventory_empty")
    return {
        "schema": "cifereca.task-admission-inventory/1", "source_sha": sha,
        "files_scanned": count, "sites": rows,
        "unresolved": sum(row["worker_resolution"] == "unresolved" for row in rows),
        "navigation_flagged": sum(bool(row["navigation_signals"]) for row in rows),
    }


if __name__ == "__main__":
    print(json.dumps(tracked_inventory(), ensure_ascii=True, indent=2, sort_keys=True))
