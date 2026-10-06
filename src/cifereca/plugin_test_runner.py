from __future__ import annotations
import argparse, ast, json, os, subprocess, sys, tempfile
from pathlib import Path
from cifereca.plugins.manifest import load_manifest

ALLOWED_PREFIX = "cifereca.plugin_api"

def private_imports(root: Path) -> list[str]:
    issues=[]
    for path in root.rglob("*.py"):
        if any(part.startswith('.') for part in path.parts): continue
        try: tree=ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except Exception: continue
        for node in ast.walk(tree):
            names=[]
            if isinstance(node, ast.Import): names=[a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module: names=[node.module]
            for name in names:
                if name == "cifereca" or (name.startswith("cifereca.") and not name.startswith(ALLOWED_PREFIX)):
                    issues.append(f"{path.relative_to(root)}:{getattr(node,'lineno',0)}: {name}")
    return issues

def main(argv=None) -> int:
    ap=argparse.ArgumentParser(description="Validate and test one Cifereca plugin package in isolation")
    ap.add_argument("package", type=Path)
    ap.add_argument("--tests", type=Path)
    ns=ap.parse_args(argv)
    root=ns.package.resolve()
    manifest=load_manifest(root/'cifereca-plugin.json')
    issues=private_imports(root)
    report={"package":manifest.package.id,"version":manifest.package.version,"private_imports":issues,"tests":None}
    if issues:
        print(json.dumps(report,ensure_ascii=False,indent=2)); return 2
    if ns.tests:
        with tempfile.TemporaryDirectory(prefix='cifereca-plugin-test-') as td:
            env=os.environ.copy(); env.update({"HOME":td,"XDG_CONFIG_HOME":td+'/config',"XDG_CACHE_HOME":td+'/cache',"XDG_DATA_HOME":td+'/data',"PYTHONPATH":str(root)+os.pathsep+env.get('PYTHONPATH','')})
            cp=subprocess.run([sys.executable,"-m","pytest","-q",str(ns.tests.resolve())],env=env,check=False)
            report["tests"]={"returncode":cp.returncode}
            print(json.dumps(report,ensure_ascii=False,indent=2)); return cp.returncode
    print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
if __name__ == '__main__': raise SystemExit(main())
