"""Temporary read-only test inventory via pytest's actual collection hook."""
from __future__ import annotations
import collections
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COREUI_TOKENS = ("core_ui", "coreui", "gui", "gtk", "ui_", "_ui", "settings", "detail_panel", "home_page", "layout", "widget", "window", "dialog", "view", "navigation", "shell", "display", "cover", "sync_attention", "borrow_controller", "borrow_status", "search_panel", "library_panel", "facet", "theme")
PROVIDERS = (
    ("biblioaccess", ("biblioaccess", "la_rochelle", "larochelle")),
    ("bnf_pret", ("bnf_pret", "bnf_loan", "bnf_borrow", "bnf_acsm")),
    ("bnf_catalogue", ("bnf_catalogue", "bnf_sru")),
    ("kobo", ("kobo", "kvault", "vault")),
    ("numilog", ("numilog",)),
    ("paris", ("paris", "bibliotheque_paris")),
    ("gallica", ("gallica",)),
    ("gutenberg", ("gutenberg",)),
    ("ebooksgratuits", ("ebooksgratuits",)),
    ("baen", ("baen",)),
    ("opds", ("opds",)),
)
DOMAINS = (
    ("sync_convergence", ("sync", "converg", "opening", "refresh", "scheduler", "snapshot", "inventory", "materializ")),
    ("acquisition_download", ("acquis", "download", "delivery", "fulfill", "acsm", "artifact", "adept", "drm", "dedup")),
    ("lending", ("loan", "borrow", "lending", "renew", "return", "emprunt")),
    ("catalog_search", ("search", "catalog", "opds", "facet", "query", "notice", "bibliograph")),
    ("accounts_settings", ("account", "settings", "credential", "auth", "consent", "identity", "profile")),
    ("library_storage", ("library", "sqlite", "storage", "db_", "ledger", "provenance", "source_reference", "placement")),
    ("diagnostics", ("diagnostic", "journal", "telemetry", "event", "report", "trace", "logging")),
    ("plugin_contracts", ("plugin", "provider", "capabilit", "manifest", "registry", "packag")),
    ("ci_quality", ("ci_", "_ci", "pytest", "workflow", "release", "installer", "quality", "test_selection")),
    ("interface_ux", ("gui", "coreui", "core_ui", "ui_", "widget", "window", "page", "layout", "view", "gtk", "theme")),
    ("device_transfer", ("usb", "device", "reader", "webdav", "transfer", "opds_pull")),
    ("recommendations", ("recommend", "optimizer", "critic", "suggestion", "rating")),
)
def provider_for(s):
    found = [name for name,aliases in PROVIDERS if any(x in s for x in aliases)]
    return found[0] if len(found)==1 else ("multi_provider" if found else "transverse")
def domain_for(s):
    for key, tokens in DOMAINS:
        if any(t in s for t in tokens): return key
    return "other_unclassified"
def architecture_for(path, package=None):
    if package:
        if package.startswith("provider-") or package in ("baen","ebooksgratuits"): return "plugins/providers"
        if package.startswith("runtime-") or "transport" in package: return "plugins/runtime"
        if package.endswith("-page") or package in ("recommendations","epub_optimizer"): return "plugins/ui_tools"
        return "plugins/other"
    low=path.lower()
    return "coreui" if any(token in low for token in COREUI_TOKENS) else "core"
class Capture:
    def __init__(self): self.records=[];self.errors=[]
    def pytest_collection_finish(self, session):
        for item in session.items:
            path = str(item.path)
            try: relative=str(Path(path).resolve().relative_to(ROOT))
            except ValueError:relative=path
            self.records.append((relative, item.nodeid))
    def pytest_collectreport(self, report):
        if report.failed:self.errors.append(report.nodeid + " | " + str(report.longrepr).splitlines()[-1][:190])
def summarize(rows, errors, section, package=None):
    res=collections.Counter(); by_provider=collections.Counter(); by_domain=collections.Counter()
    cross=collections.defaultdict(collections.Counter); files=collections.Counter()
    examples=collections.defaultdict(list)
    for path,nodeid in rows:
        signal=Path(path).stem.lower().replace("-","_").removeprefix("test_")
        architecture=architecture_for(signal,package)
        provider=provider_for(signal)
        if package and architecture=="plugins/providers":provider=package.removeprefix("provider-").replace("-","_")
        domain=domain_for(signal)
        if domain=="other_unclassified":
            method=nodeid.split("::")[-1].split("[",1)[0].lower().removeprefix("test_")
            domain=domain_for(method)
        res[architecture]+=1;by_provider[provider]+=1;by_domain[domain]+=1
        cross[architecture][domain]+=1
        files[path]+=1
        if len(examples[domain])<3:examples[domain].append(path)
    return {"section":section,"package":package,"collected":len(rows),"collection_errors":errors,
            "architecture":dict(sorted(res.items())),"provider":dict(sorted(by_provider.items())),
            "business":dict(sorted(by_domain.items())),
            "architecture_by_business":{k:dict(sorted(v.items())) for k,v in sorted(cross.items())},
            "modules":len(files),"top_files":files.most_common(12),
            "domain_examples":dict(examples)}
def collect(args,section,package=None):
    sys.path.insert(0,str(Path.cwd()))
    import pytest
    hook=Capture()
    with open(os.devnull,"w") as sink:
        from contextlib import redirect_stdout,redirect_stderr
        with redirect_stdout(sink),redirect_stderr(sink):
            result=pytest.main(["-q","--collect-only",*args],plugins=[hook])
    out=summarize(hook.records,hook.errors,section,package)
    out["exit_code"]=int(result)
    return out
def main():
    if len(sys.argv)>1 and sys.argv[1]=="single":
        pkg=sys.argv[2] if len(sys.argv)>2 else None
        out=collect(["tests"] if pkg is None else ["tests"],"core" if pkg is None else "plugin",pkg)
        print("TEST_INVENTORY="+json.dumps(out,ensure_ascii=False,sort_keys=True))
        return 0
    env=dict(os.environ)
    env["PYTHONPATH"]=str(ROOT/"src")+os.pathsep+str(ROOT/"src/plugins/provider-kobo")
    result=subprocess.run([sys.executable,str(Path(__file__).resolve()),"single"],cwd=ROOT,env=env,
        capture_output=True,text=True,timeout=180)
    all_results=[]
    for line in result.stdout.splitlines():
        if line.startswith("TEST_INVENTORY="):all_results.append(json.loads(line.split("=",1)[1]))
    for package in sorted((ROOT/"src/plugins").iterdir()):
        if not (package/"cifereca-plugin.json").is_file() or not (package/"tests").is_dir():continue
        env=dict(os.environ);env["PYTHONPATH"]=str(package)+os.pathsep+str(ROOT/"src")
        try:
            p=subprocess.run([sys.executable,str(Path(__file__).resolve()),"single",package.name],
                cwd=package,env=env,capture_output=True,text=True,timeout=90)
            lines=[s for s in p.stdout.splitlines() if s.startswith("TEST_INVENTORY=")]
            if lines:all_results.append(json.loads(lines[-1].split("=",1)[1]))
            else:all_results.append({"section":"plugin","package":package.name,"collected":0,
                         "collection_errors":["missing_result"],"stderr":p.stderr[-600:]})
        except subprocess.TimeoutExpired:
            all_results.append({"section":"plugin","package":package.name,"collected":0,"collection_errors":["timeout"]})
    agg_arch=collections.Counter();agg_provider=collections.Counter();agg_business=collections.Counter();agg_cross=collections.defaultdict(collections.Counter)
    for r in all_results:
        agg_arch.update(r.get("architecture",{}));agg_provider.update(r.get("provider",{}));agg_business.update(r.get("business",{}))
        for a,domains in r.get("architecture_by_business",{}).items():agg_cross[a].update(domains)
    print("TOTAL_TEST_INVENTORY="+json.dumps({
        "collected_total":sum(x.get("collected",0) for x in all_results),
        "core":all_results[0] if all_results else None,
        "plugins":all_results[1:],
        "by_architecture":dict(agg_arch),"by_provider":dict(agg_provider),
        "by_business":dict(agg_business),
        "architecture_by_business":{k:dict(v) for k,v in agg_cross.items()},
        "limits":"Exclusive primary business family based on test module basename, with test function name fallback for unknown names; unclassified retained; CoreUI by basename/path heuristic, not import graph. Provider plugin counts by package ownership; Core provider tests inferred from basename; E2E and simulation separate. Errors lower bound."
    },ensure_ascii=False,sort_keys=True))
if __name__=="__main__":main()
