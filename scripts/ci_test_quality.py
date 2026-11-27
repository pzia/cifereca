#!/usr/bin/env python3
"""Read-only test quality triage (#8216); signals are not deletion decisions."""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tokenize
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEST = re.compile(r"^(?:tests|src/plugins/[^/]+/tests|simulation/tests|scripts|e2e)/(?:.*/)?test[^/]*\.py$")


def dump(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def test_functions(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test"):
                    yield node.name + "." + method.name, method


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return call_name(node.value) + "." + node.attr
    return ""


def local_nodes(fn):
    """Do not attribute assertions of a nested helper to the test itself."""
    stack = list(reversed(fn.body))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))


def equality(left, right):
    # Reflexivity is a candidate only: NaN and overloaded comparisons exist.
    return isinstance(left, (ast.Name, ast.Constant)) and dump(left) == dump(right)


def delegated_oracle(tree, fn):
    """Recognize direct, unshadowed module helper calls; not proof of execution."""
    if not isinstance(fn, ast.FunctionDef):
        return False
    bindings = Counter(n.name for n in tree.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    helpers = {n.name: n for n in tree.body
               if isinstance(n, ast.FunctionDef) and bindings[n.name] == 1
               and not n.decorator_list and not n.name.startswith("test")}
    # Conservatively exclude any other binding or import of a helper name.
    shadowed = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
                and isinstance(n.ctx, (ast.Store, ast.Del))}
    shadowed.update(n.arg for n in ast.walk(fn) if isinstance(n, ast.arg))
    shadowed.update(n.asname or n.name.split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.alias))
    shadowed.update(n.name for n in ast.walk(fn)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    for stmt in fn.body:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        target = stmt.value.func
        if not isinstance(target, ast.Name) or target.id in shadowed:
            continue
        helper = helpers.get(target.id)
        if helper is None:
            continue
        # A direct nonconstant assert or a pytest.raises/warns context.
        for node in helper.body:
            if isinstance(node, ast.Assert) and not isinstance(node.test, ast.Constant):
                if not (isinstance(node.test, ast.Compare) and len(node.test.ops) == 1
                        and isinstance(node.test.ops[0], (ast.Eq, ast.Is))
                        and equality(node.test.left, node.test.comparators[0])):
                    return True
            if isinstance(node, ast.With) and any(
                isinstance(item.context_expr, ast.Call)
                and call_name(item.context_expr) in {"pytest.raises", "pytest.warns"}
                for item in node.items
            ):
                return True
    return False


def analyze(path: str, source: str) -> dict[str, Any]:
    tree = ast.parse(source, filename=path)
    findings = []
    fingerprints = []
    for name, fn in test_functions(tree):
        nodes = list(local_nodes(fn))
        assertions = [n for n in nodes if isinstance(n, ast.Assert) or
                      (isinstance(n, ast.Call) and
                       (call_name(n).split(".")[-1].startswith("assert") or
                        call_name(n) in {"pytest.raises", "pytest.warns"}))]
        def signal(kind, node):
            findings.append({"path": path, "test": name, "kind": kind, "line": node.lineno})
        for d in fn.decorator_list:
            if call_name(d) in {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail",
                                "unittest.skip", "unittest.skipIf", "unittest.skipUnless",
                                "unittest.expectedFailure"}:
                signal("skip_or_xfail_review", d)
        configured = {}
        for n in nodes:
            if isinstance(n, ast.Assign) and len(n.targets) == 1:
                target = n.targets[0]
                if (isinstance(target, ast.Attribute) and target.attr == "return_value"
                        and isinstance(target.value, ast.Name) and isinstance(n.value, ast.Constant)):
                    configured.setdefault(target.value.id, []).append(n.value)
        for n in assertions:
            expr = n.test if isinstance(n, ast.Assert) else None
            if isinstance(expr, ast.Constant) and bool(expr.value):
                signal("constant_truthy_assertion", n)
            elif isinstance(expr, ast.Compare) and len(expr.ops) == 1 and isinstance(expr.ops[0], (ast.Eq, ast.Is)):
                left, right = expr.left, expr.comparators[0]
                if equality(left, right):
                    signal("reflexive_comparison", n)
                if isinstance(expr.ops[0], ast.Eq):
                    for call, expected in ((left, right), (right, left)):
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                            values = configured.get(call.func.id, [])
                            if len(values) == 1 and isinstance(expected, ast.Constant) and dump(values[0]) == dump(expected):
                                signal("configured_return_assertion", n)
                                break
            elif isinstance(n, ast.Call) and call_name(n).endswith(".assertEqual") and len(n.args) >= 2:
                if equality(*n.args[:2]):
                    signal("reflexive_comparison", n)
            if len(assertions) == 1:
                weak = (
                    isinstance(expr, ast.Compare) and len(expr.ops) == 1
                    and isinstance(expr.ops[0], ast.IsNot)
                    and isinstance(expr.comparators[0], ast.Constant)
                    and expr.comparators[0].value is None
                ) or (
                    isinstance(n, ast.Call) and call_name(n).endswith((".assertIsNotNone", ".assertIsInstance"))
                ) or (
                    isinstance(expr, ast.Call) and call_name(expr) == "isinstance"
                )
                if weak:
                    signal("sole_weak_shape_assertion", n)
        if not assertions:
            signal("delegated_oracle_review" if delegated_oracle(tree, fn)
                   else "no_recognized_assertion", fn)
        # Exact normalized AST, not alpha-renaming. Preserve parameters and
        # decorators; identical bodies in different fixture/package scopes
        # remain candidates rather than interchangeable cases.
        fn.name = "test"
        fingerprints.append({"path": path, "test": name, "line": fn.lineno,
                             "sha256": hashlib.sha256(dump(fn).encode()).hexdigest()})
    return {"findings": findings, "fingerprints": fingerprints}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def paths_from(data: bytes) -> list[str]:
    return sorted(p for p in data.decode().split("\0") if TEST.fullmatch(p))


def snapshot(paths: list[str], ref: str | None = None) -> dict[str, Any]:
    findings, errors = [], []
    groups = defaultdict(list)
    functions = 0
    for path in paths:
        try:
            if ref:
                # Decode according to the Python source encoding declaration.
                import io
                raw = git("show", f"{ref}:{path}")
                encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                source = raw.decode(encoding)
            else:
                file = ROOT / path
                if file.is_symlink():
                    raise ValueError("symlinked test source")
                with tokenize.open(file) as stream:
                    source = stream.read()
            result = analyze(path, source)
        except (OSError, UnicodeError, SyntaxError, ValueError, subprocess.CalledProcessError) as exc:
            errors.append({"path": path, "error": type(exc).__name__})
            continue
        findings.extend(result["findings"])
        for row in result["fingerprints"]:
            groups[row["sha256"]].append(row)
            functions += 1
    return {"files": len(paths), "test_definitions": functions, "findings": findings,
            "duplicate_ast_groups": [rows for _, rows in sorted(groups.items()) if len(rows) > 1],
            "errors": errors}


def new_findings(before: list, after: list) -> list:
    def key(row):
        return row["path"], row["test"], row["kind"]
    available = Counter(map(key, before))
    new = []
    for row in after:
        k = key(row)
        if available[k]:
            available[k] -= 1
        else:
            new.append(row)
    return new


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if bool(args.base_ref) != bool(args.head_ref):
        parser.error("base-ref and head-ref must be supplied together")
    if args.base_ref:
        head = git("rev-parse", "--verify", args.head_ref + "^{commit}").decode().strip()
        base = git("merge-base", args.base_ref, head).decode().strip()
        paths = paths_from(git("diff", "--name-only", "-z", "--no-renames", base, head))
        # Intersect with each tree so additions/deletions are not read errors.
        old = set(paths_from(git("ls-tree", "-r", "--name-only", "-z", base)))
        current = set(paths_from(git("ls-tree", "-r", "--name-only", "-z", head)))
        before = snapshot([p for p in paths if p in old], base)
        after = snapshot([p for p in paths if p in current], head)
        report = {"scope": "changed_test_files", "source_sha": head, "base_sha": base,
                  **after, "new_findings": new_findings(before["findings"], after["findings"]),
                  "base_errors": before["errors"]}
    else:
        head = git("rev-parse", "HEAD").decode().strip()
        if git("status", "--porcelain", "--untracked-files=no"):
            parser.error("full inventory requires a clean tracked worktree")
        report = {"scope": "tracked_test_files", "source_sha": head,
                  **snapshot(paths_from(git("ls-files", "-z")))}
        if git("rev-parse", "HEAD").decode().strip() != head or git("status", "--porcelain", "--untracked-files=no"):
            parser.error("worktree changed during inventory")
    report["schema"] = "cifereca.test-quality-candidates/1"
    report["note"] = "Static candidates only; not collected cases, coverage or proof of obsolete/redundant behavior."
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("scope", "source_sha", "files", "test_definitions", "errors")}))
    print("Candidate counts:", dict(Counter(row["kind"] for row in report.get("new_findings", report["findings"]))))
    print("Identical AST groups in scope:", len(report["duplicate_ast_groups"]))
    if args.base_ref:
        for row in report["new_findings"]:
            print(f"{row['path']}:{row['line']} {row['test']} {row['kind']}")
    return int(bool(report["errors"] or report.get("base_errors")))


if __name__ == "__main__":
    raise SystemExit(main())
