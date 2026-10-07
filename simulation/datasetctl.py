#!/usr/bin/env python3
"""Orchestrateur des jeux de données UX Cifereca.

Exemples :
    python3 simulation/datasetctl.py profiles
    python3 simulation/datasetctl.py up ux-search
    python3 simulation/datasetctl.py status ux-search
    python3 simulation/datasetctl.py cifereca ux-search
    python3 simulation/datasetctl.py exec ux-library -- capture-app ...
    python3 simulation/datasetctl.py down ux-search
    python3 simulation/datasetctl.py rebuild ux-search

Chaque profil dispose de ses propres XDG_CONFIG/DATA/CACHE/STATE afin de ne
jamais modifier la configuration ni la bibliothèque Cifereca réelles.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    import tomllib
except ImportError as exc:  # pragma: no cover - Python < 3.11
    raise SystemExit("datasetctl nécessite Python 3.11+ (tomllib)") from exc


ROOT = Path(__file__).resolve().parent
CATALOG_FILE = ROOT / "services" / "catalog.toml"
PROFILES_DIR = ROOT / "profiles"
DEFAULT_RUNTIME_ROOT = ROOT / ".runtime"
RUNTIME_ENV = "CIFERECA_DATASET_RUNTIME"


class DatasetError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DatasetError(f"TOML invalide ou inaccessible: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"TOML racine invalide: {path}")
    return value


def profile_path(value: str) -> Path:
    raw = Path(value).expanduser()
    if raw.exists():
        return raw.resolve()
    name = value[:-5] if value.endswith(".toml") else value
    candidate = PROFILES_DIR / f"{name}.toml"
    if not candidate.exists():
        raise DatasetError(f"profil inconnu: {value}")
    return candidate.resolve()


def load_profile(value: str) -> tuple[Path, dict]:
    path = profile_path(value)
    profile = load_toml(path)
    name = str(profile.get("name") or path.stem).strip()
    if not name or not all(ch.isalnum() or ch in "-_" for ch in name):
        raise DatasetError("profile.name doit être un identifiant simple")
    profile["name"] = name
    for section in ("runtime", "cifereca", "library", "services"):
        if section in profile and not isinstance(profile[section], dict):
            raise DatasetError(f"[{section}] doit être une table TOML")
    return path, profile


def load_catalog() -> dict[str, dict]:
    root = load_toml(CATALOG_FILE)
    if int(root.get("version") or 0) != 1:
        raise DatasetError("version de catalogue de services non supportée")
    services = root.get("service")
    if not isinstance(services, dict):
        raise DatasetError("[service] absent de services/catalog.toml")
    return services


def runtime_root() -> Path:
    return Path(os.environ.get(RUNTIME_ENV, str(DEFAULT_RUNTIME_ROOT))).expanduser().resolve()


def profile_runtime(profile: dict) -> Path:
    return runtime_root() / str(profile["name"])


def state_path(profile: dict) -> Path:
    return profile_runtime(profile) / "runtime.json"


def load_state(profile: dict) -> dict:
    path = state_path(profile)
    if not path.exists():
        return {"version": 1, "profile": profile["name"], "services": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "profile": profile["name"], "services": {}}
    if not isinstance(value, dict):
        value = {}
    value.setdefault("version", 1)
    value.setdefault("profile", profile["name"])
    if not isinstance(value.get("services"), dict):
        value["services"] = {}
    return value


def save_state(profile: dict, state: dict) -> None:
    path = state_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def managed_service_alive(row: dict) -> bool:
    try:
        pid = int(row.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if not is_pid_alive(pid):
        return False
    command = row.get("command")
    if not isinstance(command, list) or len(command) < 2:
        return False
    expected_script = str(command[1])
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            args = [
                part.decode("utf-8", errors="replace")
                for part in proc_cmdline.read_bytes().split(b"\0")
                if part
            ]
        except OSError:
            return False
        return expected_script in args
    # Non-Linux fallback: keep PID semantics but never infer another command.
    return True


def health_ok(url: str, timeout: float = 0.7) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 400
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False


def health_payload(url: str, timeout: float = 0.7) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if not 200 <= int(response.status) < 400:
                return None
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
        return None


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def enabled_service_configs(profile: dict) -> dict[str, dict]:
    rows = profile.get("services") or {}
    return {
        str(name): dict(config)
        for name, config in rows.items()
        if isinstance(config, dict) and bool(config.get("enabled", False))
    }


def service_context(profile: dict, catalog: dict[str, dict], name: str, config: dict) -> dict:
    spec = catalog.get(name)
    if not isinstance(spec, dict):
        raise DatasetError(f"service inconnu dans le profil: {name}")
    script = ROOT / str(spec.get("script") or "")
    if not script.is_file():
        raise DatasetError(f"script du service {name} absent: {script}")
    port = int(config.get("port") or spec.get("default_port") or 0)
    if not 1 <= port <= 65535:
        raise DatasetError(f"port invalide pour {name}: {port}")
    health_path = str(spec.get("health_path") or "/health")
    if not health_path.startswith("/"):
        raise DatasetError(f"health_path invalide pour {name}")
    base_url = f"http://127.0.0.1:{port}"
    return {
        "name": name,
        "spec": spec,
        "config": config,
        "script": script,
        "port": port,
        "base_url": base_url,
        "health_url": base_url + health_path,
    }


def build_service_command(profile: dict, context: dict, *, rebuild: bool) -> list[str]:
    runtime = profile_runtime(profile)
    name = context["name"]
    spec = context["spec"]
    config = context["config"]
    command = [
        sys.executable,
        str(context["script"]),
        "--bind",
        "127.0.0.1",
        "--port",
        str(context["port"]),
        "--cache-dir",
        str(runtime / "service-cache" / name),
    ]
    arg_map = spec.get("args") or {}
    if not isinstance(arg_map, dict):
        raise DatasetError(f"service {name}: [args] invalide")
    for key, flag in arg_map.items():
        if key not in config:
            continue
        value = config[key]
        if isinstance(value, bool):
            if value:
                command.append(str(flag))
        elif value is not None:
            command.extend((str(flag), str(value)))
    if rebuild:
        command.append("--rebuild")
    return command


def stop_pid(pid: int, *, timeout: float = 4.0) -> None:
    if not is_pid_alive(pid):
        return
    try:
        if os.name == "posix":
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        if os.name == "posix":
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def stop_service(profile: dict, state: dict, name: str) -> None:
    row = (state.get("services") or {}).get(name)
    if not isinstance(row, dict):
        return
    try:
        pid = int(row.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if managed_service_alive(row):
        stop_pid(pid)
    state["services"].pop(name, None)


def generate_local_library(profile: dict, *, rebuild: bool = False) -> Path:
    runtime = profile_runtime(profile)
    library = runtime / "library"
    config = profile.get("library") or {}
    count = int(config.get("epubs") or 0)
    seed = int(config.get("seed") or 20261006)
    marker = library / ".dataset.json"
    wanted = {"version": 1, "epubs": count, "seed": seed}

    current = None
    if marker.exists():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = None

    if rebuild or current != wanted:
        if library.exists():
            shutil.rmtree(library)
        library.mkdir(parents=True, exist_ok=True)
        if count:
            generator = ROOT / "Epub" / "gen_fake_epubs.py"
            log_dir = runtime / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "library-generator.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    [sys.executable, str(generator), "-n", str(count), "-o", str(library), "--seed", str(seed)],
                    cwd=str(ROOT.parent),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            if result.returncode:
                raise DatasetError(f"génération EPUB échouée (voir {log_dir / 'library-generator.log'})")
        marker.write_text(json.dumps(wanted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        library.mkdir(parents=True, exist_ok=True)
    return library


def default_host_data_home() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base.expanduser().resolve()


def host_plugin_store(profile: dict) -> Path:
    explicit = os.environ.get("CIFERECA_DATASET_PLUGIN_SOURCE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    candidate = default_host_data_home() / "cifereca" / "plugins" / "packages"
    target = profile_runtime(profile) / "xdg" / "data" / "cifereca" / "plugins" / "packages"
    try:
        if candidate.resolve() == target.resolve() or profile_runtime(profile) in candidate.resolve().parents:
            candidate = (Path.home() / ".local" / "share" / "cifereca" / "plugins" / "packages").resolve()
    except OSError:
        pass
    return candidate


def plugin_store_signature(source: Path) -> str:
    digest = hashlib.sha256()
    if not source.is_dir():
        return ""
    for package in sorted(path for path in source.iterdir() if path.is_dir()):
        digest.update(package.name.encode("utf-8", "surrogatepass"))
        pointer = package / "current.json"
        if pointer.is_file():
            digest.update(pointer.read_bytes())
        for version in sorted(path for path in package.iterdir() if path.is_dir()):
            digest.update(version.name.encode("utf-8", "surrogatepass"))
            for filename in ("cifereca-plugin.json", ".cifereca-install.json"):
                item = version / filename
                if item.is_file():
                    digest.update(item.read_bytes())
    return digest.hexdigest()


def sync_plugin_packages(profile: dict) -> Path | None:
    cfg = profile.get("cifereca") or {}
    if cfg.get("copy_installed_plugins", True) is False:
        return None
    source = host_plugin_store(profile)
    if not source.is_dir():
        return None

    runtime = profile_runtime(profile)
    target = runtime / "xdg" / "data" / "cifereca" / "plugins" / "packages"
    marker = runtime / "plugin-packages.json"
    signature = plugin_store_signature(source)
    current = {}
    if marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    if target.is_dir() and current.get("source") == str(source) and current.get("signature") == signature:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary, symlinks=True)
    if target.exists():
        shutil.rmtree(target)
    temporary.replace(target)
    marker.write_text(
        json.dumps({"source": str(source), "signature": signature, "copied_at": now_iso()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def isolated_env(profile: dict) -> dict[str, str]:
    runtime = profile_runtime(profile)
    xdg = runtime / "xdg"
    env = dict(os.environ)
    env.update({
        "XDG_CONFIG_HOME": str(xdg / "config"),
        "XDG_DATA_HOME": str(xdg / "data"),
        "XDG_CACHE_HOME": str(xdg / "cache"),
        "XDG_STATE_HOME": str(xdg / "state"),
        "CIFERECA_UX_PROFILE": str(profile["name"]),
    })
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def service_urls(profile: dict, catalog: dict[str, dict]) -> dict[str, str]:
    out = {}
    for name, config in enabled_service_configs(profile).items():
        ctx = service_context(profile, catalog, name, config)
        out[name] = ctx["base_url"]
    return out


def write_cifereca_config(profile: dict, catalog: dict[str, dict], library: Path) -> tuple[Path, Path]:
    runtime = profile_runtime(profile)
    env = isolated_env(profile)
    config_dir = Path(env["XDG_CONFIG_HOME"]) / "cifereca"
    config_dir.mkdir(parents=True, exist_ok=True)
    urls = service_urls(profile, catalog)
    accounts = []

    if "kobo" in urls:
        accounts.append({
            "id": "ux-kobo",
            "identity_id": "ux",
            "provider": "kobo",
            "label": "Kobo UX",
            "enabled": True,
            "credentials": {"api_token": "kvt_test"},
            "options": {"base_url": urls["kobo"], "display_profile": "Android"},
        })
    if "paris" in urls:
        accounts.append({
            "id": "ux-paris",
            "identity_id": "ux",
            "provider": "paris",
            "label": "Paris UX",
            "enabled": True,
            "credentials": {"username": "paris-test", "password": "test"},
            "options": {"base_url": urls["paris"]},
        })
    if "numilog" in urls:
        accounts.append({
            "id": "ux-numilog",
            "identity_id": "ux",
            "provider": "numilog",
            "label": "Numilog UX",
            "enabled": True,
            "credentials": {"email": "numilog-test@example.test", "password": "test"},
            "options": {"base_url": urls["numilog"] + "/"},
        })

    ui = profile.get("cifereca") or {}
    config = {
        "identities": [{
            "id": "ux",
            "label": f"UX — {profile['name']}",
            "directory_name": "ux",
            "preferences": {"prefer_original_language": False, "reading_languages": ["fr"]},
        }],
        "providers": {
            "accounts": accounts,
            "settings": {},
            "gallica_migration_031": True,
        },
        "downloads": {
            "directory": str(runtime / "downloads"),
            "content_type_directories": {"ebook": "", "audiobook": ""},
            "command": [],
            "post_download_command": [],
        },
        "diagnostics": {"enabled": True, "level": "detailed"},
        "ui": {
            "width": int(ui.get("width") or 1300),
            "height": int(ui.get("height") or 850),
        },
        "library": {"directory": str(library)},
        "cache": {
            "dashboard_seconds": 60,
            "history_seconds": 60,
            "search_seconds": 300,
            "holdings_seconds": 60,
        },
    }
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    plugin_values = {}
    def plugin(plugin_id: str, values: dict) -> None:
        plugin_values[plugin_id] = {"plugin": {"_": values}}

    if "opds" in urls:
        plugin("provider.gallica", {"opds_url": urls["opds"] + "/gallica/opds"})
        plugin("provider.gutenberg", {
            "contact": "ux-dataset@localhost.invalid",
            "opds_search_url": urls["opds"] + "/gutenberg/ebooks/search.opds/",
        })
    if "bnf" in urls:
        plugin("provider.bnf-catalogue", {"sru_url": urls["bnf"] + "/api/SRU"})
    if "ebooksgratuits" in urls:
        plugin("provider.ebooksgratuits", {"base_url": urls["ebooksgratuits"] + "/"})
    if "numilog" in urls:
        plugin("provider.numilog", {"base_url": urls["numilog"] + "/"})

    plugins_config_path = config_dir / "plugins-config.json"
    plugins_config_path.write_text(
        json.dumps({"version": 1, "values": plugin_values}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return config_path, plugins_config_path


def prepare(profile: dict, catalog: dict[str, dict], *, rebuild_library: bool = False) -> dict:
    runtime = profile_runtime(profile)
    (runtime / "logs").mkdir(parents=True, exist_ok=True)
    (runtime / "downloads").mkdir(parents=True, exist_ok=True)
    library = generate_local_library(profile, rebuild=rebuild_library)
    plugin_packages = sync_plugin_packages(profile)
    config_path, plugins_path = write_cifereca_config(profile, catalog, library)
    return {
        "runtime": runtime,
        "library": library,
        "plugin_packages": plugin_packages,
        "config": config_path,
        "plugins_config": plugins_path,
        "env": isolated_env(profile),
    }


def start_services(profile: dict, catalog: dict[str, dict], *, rebuild: bool = False) -> dict:
    prepared = prepare(profile, catalog, rebuild_library=rebuild)
    state = load_state(profile)
    services = enabled_service_configs(profile)
    startup_timeout = float((profile.get("runtime") or {}).get("startup_timeout_seconds") or 30)

    for old_name in list((state.get("services") or {}).keys()):
        if old_name not in services:
            stop_service(profile, state, old_name)

    for name, config in services.items():
        ctx = service_context(profile, catalog, name, config)
        desired_command = build_service_command(profile, ctx, rebuild=False)
        launch_command = build_service_command(profile, ctx, rebuild=rebuild)
        old = (state.get("services") or {}).get(name)
        if isinstance(old, dict):
            old_pid = int(old.get("pid") or 0)
            same = old.get("command") == desired_command
            if same and managed_service_alive(old) and health_ok(ctx["health_url"]):
                print(f"[ok] {name}: déjà actif pid={old_pid} {ctx['base_url']}")
                continue
            stop_service(profile, state, name)

        if port_in_use(ctx["port"]):
            raise DatasetError(f"{name}: port {ctx['port']} déjà utilisé par un processus hors datasetctl")

        log_path = prepared["runtime"] / "logs" / f"{name}.log"
        log = log_path.open("a", encoding="utf-8")
        log.write(f"\n=== {now_iso()} start {' '.join(launch_command)} ===\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                launch_command,
                cwd=str(ROOT.parent),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        finally:
            log.close()

        row = {
            "pid": proc.pid,
            "port": ctx["port"],
            "base_url": ctx["base_url"],
            "health_url": ctx["health_url"],
            "command": desired_command,
            "launch_command": launch_command,
            "log": str(log_path),
            "started_at": now_iso(),
        }
        state["services"][name] = row
        save_state(profile, state)

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise DatasetError(f"{name}: arrêt prématuré rc={proc.returncode}; voir {log_path}")
            if health_ok(ctx["health_url"]):
                print(f"[ok] {name}: pid={proc.pid} {ctx['base_url']}")
                break
            time.sleep(0.2)
        else:
            stop_service(profile, state, name)
            save_state(profile, state)
            raise DatasetError(f"{name}: health check timeout; voir {log_path}")

    state["prepared_at"] = now_iso()
    state["profile_file"] = str(profile.get("_path") or "")
    save_state(profile, state)
    return prepared


def down(profile: dict) -> None:
    state = load_state(profile)
    for name in list((state.get("services") or {}).keys()):
        row = state["services"].get(name) or {}
        pid = int(row.get("pid") or 0)
        stop_service(profile, state, name)
        print(f"[stop] {name}: pid={pid}")
    save_state(profile, state)


def reset_runtime(profile: dict) -> None:
    down(profile)
    runtime = profile_runtime(profile)
    for child in ("service-cache", "library", "downloads", "xdg", "logs"):
        path = runtime / child
        if path.exists():
            shutil.rmtree(path)
    state = state_path(profile)
    state.unlink(missing_ok=True)
    (runtime / "plugin-packages.json").unlink(missing_ok=True)


def installed_plugin_ids(packages_root: Path | None) -> set[str]:
    if packages_root is None or not packages_root.is_dir():
        return set()
    out: set[str] = set()
    for package in packages_root.iterdir():
        if not package.is_dir():
            continue
        pointer = package / "current.json"
        active = None
        if pointer.is_file():
            try:
                data = json.loads(pointer.read_text(encoding="utf-8"))
                active = str(data.get("active") or "").strip() if isinstance(data, dict) else ""
            except (OSError, json.JSONDecodeError):
                active = ""
        roots = [package / active] if active else sorted(
            (path for path in package.iterdir() if path.is_dir()),
            reverse=True,
        )
        for root in roots:
            manifest = root / "cifereca-plugin.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for row in data.get("plugins") or []:
                if isinstance(row, dict) and str(row.get("id") or "").strip():
                    out.add(str(row["id"]).strip())
            break
    return out


def ensure_required_plugins(profile: dict, catalog: dict[str, dict], packages_root: Path | None) -> None:
    required = derived_plugins(profile, catalog)
    if not required:
        return
    available = installed_plugin_ids(packages_root)
    missing = [plugin_id for plugin_id in required if plugin_id not in available]
    if missing:
        source = host_plugin_store(profile)
        raise DatasetError(
            "plugins UX absents du store installé: "
            + ", ".join(missing)
            + f" ; source inspectée: {source}"
        )


def derived_plugins(profile: dict, catalog: dict[str, dict]) -> list[str]:
    plugins: list[str] = []
    for name, config in enabled_service_configs(profile).items():
        ctx = service_context(profile, catalog, name, config)
        for plugin_id in ctx["spec"].get("plugins") or []:
            if plugin_id not in plugins:
                plugins.append(str(plugin_id))
    for plugin_id in (profile.get("cifereca") or {}).get("force_plugins") or []:
        if str(plugin_id) not in plugins:
            plugins.append(str(plugin_id))
    return plugins


def cifereca_command(profile: dict, catalog: dict[str, dict], extra: list[str], verbose: bool) -> list[str]:
    cfg = profile.get("cifereca") or {}
    binary = str(cfg.get("binary") or "cifereca")
    command = [binary]
    if bool(cfg.get("no_plugins", True)):
        command.append("--no-plugins")
    for plugin_id in derived_plugins(profile, catalog):
        command.extend(("--force-plugin", plugin_id))
    if verbose or bool(cfg.get("verbose", False)):
        command.append("--verbose")
    command.extend(extra)
    return command


def printable_command(command: list[str], env: dict[str, str]) -> str:
    import shlex
    prefix = " ".join(
        f"{key}={shlex.quote(env[key])}"
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
    )
    return prefix + " " + " ".join(shlex.quote(x) for x in command)


def command_profiles(_args, _catalog) -> int:
    for path in sorted(PROFILES_DIR.glob("*.toml")):
        try:
            profile = load_toml(path)
            print(f"{path.stem:14} {profile.get('description') or ''}")
        except DatasetError as exc:
            print(f"{path.stem:14} ERREUR: {exc}")
    return 0


def command_up(args, catalog) -> int:
    path, profile = load_profile(args.profile)
    profile["_path"] = str(path)
    start_services(profile, catalog, rebuild=args.rebuild)
    prepared = prepare(profile, catalog)
    print(f"[ready] profil={profile['name']}")
    print(f"[ready] config={prepared['config']}")
    return 0


def command_down(args, _catalog) -> int:
    _, profile = load_profile(args.profile)
    down(profile)
    return 0


def status_rows(profile: dict, catalog: dict[str, dict]) -> list[dict]:
    state = load_state(profile)
    rows = []
    for name, config in enabled_service_configs(profile).items():
        ctx = service_context(profile, catalog, name, config)
        saved = (state.get("services") or {}).get(name) or {}
        pid = int(saved.get("pid") or 0)
        rows.append({
            "service": name,
            "pid": pid or None,
            "process": managed_service_alive(saved),
            "health": health_ok(ctx["health_url"]),
            "url": ctx["base_url"],
            "log": saved.get("log"),
        })
    return rows


def command_status(args, catalog) -> int:
    _, profile = load_profile(args.profile)
    rows = status_rows(profile, catalog)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        state = "UP" if row["process"] and row["health"] else "DOWN"
        print(f"{row['service']:10} {state:4} pid={row['pid'] or '-':>6} {row['url']}")
    return 0 if all(r["process"] and r["health"] for r in rows) else 1


def summary_payload(profile: dict, catalog: dict[str, dict]) -> dict:
    runtime = profile_runtime(profile)
    library = runtime / "library"
    library_cfg = profile.get("library") or {}
    configured_epubs = int(library_cfg.get("epubs") or 0)
    generated_epubs = sum(1 for path in library.glob("*.epub") if path.is_file()) if library.is_dir() else 0
    state = load_state(profile)
    services = []
    for name, config in enabled_service_configs(profile).items():
        ctx = service_context(profile, catalog, name, config)
        saved = (state.get("services") or {}).get(name) or {}
        declared = {
            key: config[key]
            for key in (ctx["spec"].get("args") or {})
            if key in config and key != "seed"
        }
        services.append({
            "service": name,
            "process": managed_service_alive(saved),
            "health": health_ok(ctx["health_url"]),
            "url": ctx["base_url"],
            "configured": declared,
            "reported": health_payload(ctx["health_url"]),
        })
    return {
        "profile": profile["name"],
        "runtime": str(runtime),
        "library": {
            "path": str(library),
            "configured_epubs": configured_epubs,
            "generated_epubs": generated_epubs,
            "complete": generated_epubs == configured_epubs,
        },
        "services": services,
    }


def command_summary(args, catalog) -> int:
    _, profile = load_profile(args.profile)
    payload = summary_payload(profile, catalog)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    library = payload["library"]
    state = "OK" if library["complete"] else "INCOMPLET"
    print(
        f"library    {state:9} generated={library['generated_epubs']} "
        f"configured={library['configured_epubs']} {library['path']}"
    )
    for row in payload["services"]:
        service_state = "UP" if row["process"] and row["health"] else "DOWN"
        configured = " ".join(f"{key}={value}" for key, value in row["configured"].items())
        reported = row["reported"] or {}
        counts = reported.get("counts") if isinstance(reported.get("counts"), dict) else {}
        if not counts:
            counts = {key: reported[key] for key in ("books", "catalog", "vault") if key in reported}
        actual = " ".join(f"{key}={value}" for key, value in counts.items())
        details = " ".join(part for part in (configured, actual) if part)
        print(f"{row['service']:10} {service_state:9} {details}".rstrip())
    return 0


def command_logs(args, _catalog) -> int:
    _, profile = load_profile(args.profile)
    logs = profile_runtime(profile) / "logs"
    if args.service:
        paths = [logs / f"{args.service}.log"]
    else:
        paths = sorted(logs.glob("*.log"))
    for path in paths:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"===== {path.name} =====")
        for line in lines[-args.tail:]:
            print(line)
    return 0


def command_config(args, catalog) -> int:
    path, profile = load_profile(args.profile)
    profile["_path"] = str(path)
    prepared = prepare(profile, catalog)
    payload = {
        "profile": profile["name"],
        "runtime": str(prepared["runtime"]),
        "library": str(prepared["library"]),
        "config": str(prepared["config"]),
        "plugins_config": str(prepared["plugins_config"]),
        "plugin_packages": str(prepared["plugin_packages"]) if prepared["plugin_packages"] else None,
        "plugins": derived_plugins(profile, catalog),
        "env": {k: prepared["env"][k] for k in ("XDG_CONFIG_HOME","XDG_DATA_HOME","XDG_CACHE_HOME","XDG_STATE_HOME")},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_env(args, catalog) -> int:
    _, profile = load_profile(args.profile)
    prepared = prepare(profile, catalog)
    for key in ("XDG_CONFIG_HOME","XDG_DATA_HOME","XDG_CACHE_HOME","XDG_STATE_HOME","CIFERECA_UX_PROFILE"):
        print(f"export {key}={json.dumps(prepared['env'][key])}")
    return 0


def command_rebuild(args, catalog) -> int:
    path, profile = load_profile(args.profile)
    profile["_path"] = str(path)
    print(f"[reset] {profile['name']}")
    reset_runtime(profile)
    start_services(profile, catalog, rebuild=True)
    print(f"[ready] profil={profile['name']}")
    return 0


def command_cifereca(args, catalog) -> int:
    path, profile = load_profile(args.profile)
    profile["_path"] = str(path)
    prepared = start_services(profile, catalog)
    extra = list(args.extra or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    ensure_required_plugins(profile, catalog, prepared["plugin_packages"])
    command = cifereca_command(profile, catalog, extra, args.verbose)
    if args.dry_run:
        print(printable_command(command, prepared["env"]))
        return 0
    if shutil.which(command[0], path=prepared["env"].get("PATH")) is None:
        raise DatasetError(f"exécutable Cifereca introuvable: {command[0]}")
    if args.detach:
        log_path = prepared["runtime"] / "logs" / "cifereca.log"
        with log_path.open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(
                command,
                env=prepared["env"],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
        print(f"[cifereca] pid={proc.pid} log={log_path}")
        return 0
    return subprocess.call(command, env=prepared["env"])


def command_exec(args, catalog) -> int:
    path, profile = load_profile(args.profile)
    profile["_path"] = str(path)
    prepared = start_services(profile, catalog)
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise DatasetError("exec exige une commande après --")
    if args.dry_run:
        print(printable_command(command, prepared["env"]))
        return 0
    return subprocess.call(command, env=prepared["env"])


def command_snapshot(args, catalog) -> int:
    path, profile = load_profile(args.profile)
    profile["_path"] = str(path)
    prepared = start_services(profile, catalog)

    harness_value = args.harness or os.environ.get("CIFERECA_GTK_TEST_HARNESS")
    if not harness_value:
        raise DatasetError("snapshot exige --harness ou CIFERECA_GTK_TEST_HARNESS")
    harness = Path(harness_value).expanduser().resolve()
    harness_python = harness / ".venv" / "bin" / "python"
    headless_py = harness / "headless.py"
    capture_py = harness / "capture_app.py"
    gtk_root = harness / "runtime" / "root"
    xvfb = gtk_root / "usr" / "bin" / "Xvfb-local"
    required = (harness_python, headless_py, capture_py, xvfb)
    if any(not path.is_file() for path in required):
        raise DatasetError(f"harnais GTK incomplet ou non bootstrappé: {harness}")

    cfg = profile.get("cifereca") or {}
    width = int(args.width or cfg.get("width") or 1300)
    height = int(args.height or cfg.get("height") or 850)
    if not (320 <= width <= 4096 and 320 <= height <= 4096):
        raise DatasetError("dimensions snapshot hors limites 320..4096")

    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ensure_required_plugins(profile, catalog, prepared["plugin_packages"])
    app_command = cifereca_command(profile, catalog, [], args.verbose)
    capture_command = [
        str(harness_python),
        str(capture_py),
        "--wait", str(args.wait),
        "--out", str(output),
    ]
    for click in args.click or []:
        capture_command.extend(("--click", click))
    if args.type_text is not None:
        capture_command.extend(("--type-text", args.type_text))
    for key in args.key or []:
        capture_command.extend(("--key", key))
    capture_command.extend(("--", *app_command))
    command = [
        str(harness_python),
        str(headless_py),
        "--screen", f"{width}x{height}x24",
        "--",
        *capture_command,
    ]

    snapshot_env = dict(prepared["env"])
    root_usr = gtk_root / "usr"
    lib = gtk_root / "usr" / "lib" / "x86_64-linux-gnu"
    lib2 = gtk_root / "lib" / "x86_64-linux-gnu"
    gi = lib / "girepository-1.0"
    py_dist = gtk_root / "usr" / "lib" / "python3" / "dist-packages"
    snapshot_env["PATH"] = str(root_usr / "bin") + os.pathsep + snapshot_env.get("PATH", "")
    snapshot_env["LD_LIBRARY_PATH"] = os.pathsep.join(
        [str(lib), str(lib2)] + ([snapshot_env["LD_LIBRARY_PATH"]] if snapshot_env.get("LD_LIBRARY_PATH") else [])
    )
    snapshot_env["GI_TYPELIB_PATH"] = os.pathsep.join(
        [str(gi)] + ([snapshot_env["GI_TYPELIB_PATH"]] if snapshot_env.get("GI_TYPELIB_PATH") else [])
    )
    snapshot_env["PYTHONPATH"] = os.pathsep.join(
        [str(py_dist)] + ([snapshot_env["PYTHONPATH"]] if snapshot_env.get("PYTHONPATH") else [])
    )
    snapshot_env["XDG_DATA_DIRS"] = str(root_usr / "share") + os.pathsep + snapshot_env.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
    )
    snapshot_env.update({
        "GTK4_POC_BASE": str(harness),
        "GTK4_POC_ROOT": str(gtk_root),
        "GDK_BACKEND": "x11",
        "GSK_RENDERER": "cairo",
        "GDK_DEBUG": "gl-disable",
        "GDK_SCALE": "1",
        "GTK_A11Y": "none",
    })
    xdg_runtime = prepared["runtime"] / "xdg-runtime"
    tmpdir = prepared["runtime"] / "tmp"
    xdg_runtime.mkdir(parents=True, exist_ok=True)
    tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        xdg_runtime.chmod(0o700)
    except OSError:
        pass
    snapshot_env["XDG_RUNTIME_DIR"] = str(xdg_runtime)
    snapshot_env["TMPDIR"] = str(tmpdir)

    if args.dry_run:
        print(printable_command(command, snapshot_env))
        return 0
    result = subprocess.call(command, env=snapshot_env)
    if result == 0:
        print(f"[snapshot] {output}")
    return result


def command_doctor(args, catalog) -> int:
    path, profile = load_profile(args.profile)
    profile["_path"] = str(path)
    problems = []
    print(f"profile : {profile['name']} ({path})")
    print(f"runtime : {profile_runtime(profile)}")
    for name, config in enabled_service_configs(profile).items():
        try:
            ctx = service_context(profile, catalog, name, config)
            print(f"service : {name:10} port={ctx['port']} script={ctx['script'].relative_to(ROOT)}")
        except DatasetError as exc:
            problems.append(str(exc))
    packages = sync_plugin_packages(profile)
    required = derived_plugins(profile, catalog)
    available = installed_plugin_ids(packages)
    missing = [plugin_id for plugin_id in required if plugin_id not in available]
    print(f"plugins  : {packages or 'aucun store installé trouvé'}")
    if missing:
        problems.append("plugins UX absents: " + ", ".join(missing))

    binary = str((profile.get("cifereca") or {}).get("binary") or "cifereca")
    found = shutil.which(binary)
    print(f"cifereca: {found or 'NON TROUVÉ (' + binary + ')'}")
    if not found:
        problems.append(f"exécutable Cifereca introuvable: {binary}")
    if problems:
        for problem in problems:
            print(f"[error] {problem}", file=sys.stderr)
        return 1
    print("[ok] profil valide")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Orchestrateur des datasets UX Cifereca")
    sub = p.add_subparsers(dest="command_name", required=True)

    sub.add_parser("profiles", help="Liste les profils UX")

    up = sub.add_parser("up", help="Prépare et démarre un profil")
    up.add_argument("profile")
    up.add_argument("--rebuild", action="store_true", help="Reconstruit les caches des services")

    down_p = sub.add_parser("down", help="Arrête les services d'un profil")
    down_p.add_argument("profile")

    rebuild = sub.add_parser("rebuild", help="Réinitialise complètement un profil puis le redémarre")
    rebuild.add_argument("profile")

    status = sub.add_parser("status", help="État des services")
    status.add_argument("profile")
    status.add_argument("--json", action="store_true")

    summary = sub.add_parser("summary", help="Résume les volumes configurés et réellement disponibles")
    summary.add_argument("profile")
    summary.add_argument("--json", action="store_true")

    logs = sub.add_parser("logs", help="Affiche la fin des logs")
    logs.add_argument("profile")
    logs.add_argument("service", nargs="?")
    logs.add_argument("--tail", type=int, default=80)

    config = sub.add_parser("config", help="Génère et affiche les chemins de configuration")
    config.add_argument("profile")

    env_p = sub.add_parser("env", help="Affiche les exports XDG du profil")
    env_p.add_argument("profile")

    doctor = sub.add_parser("doctor", help="Valide le profil, les scripts et l'exécutable")
    doctor.add_argument("profile")

    app = sub.add_parser("cifereca", help="Démarre Cifereca dans l'environnement UX isolé")
    app.add_argument("profile")
    app.add_argument("--detach", action="store_true")
    app.add_argument("--verbose", action="store_true")
    app.add_argument("--dry-run", action="store_true")
    app.add_argument("extra", nargs=argparse.REMAINDER)

    execute = sub.add_parser("exec", help="Exécute une commande quelconque dans l'environnement UX")
    execute.add_argument("profile")
    execute.add_argument("--dry-run", action="store_true")
    execute.add_argument("command", nargs=argparse.REMAINDER)

    snapshot = sub.add_parser("snapshot", help="Capture Cifereca via le harnais GTK/Xvfb")
    snapshot.add_argument("profile")
    snapshot.add_argument("--harness", help="Répertoire gtk4-environnement-tests")
    snapshot.add_argument("--out", default="snapshot.png")
    snapshot.add_argument("--wait", type=float, default=4.0)
    snapshot.add_argument("--width", type=int)
    snapshot.add_argument("--height", type=int)
    snapshot.add_argument("--click", action="append", default=[], metavar="X,Y")
    snapshot.add_argument("--type-text")
    snapshot.add_argument("--key", action="append", default=[])
    snapshot.add_argument("--verbose", action="store_true")
    snapshot.add_argument("--dry-run", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = load_catalog()
    handlers = {
        "profiles": command_profiles,
        "up": command_up,
        "down": command_down,
        "rebuild": command_rebuild,
        "status": command_status,
        "summary": command_summary,
        "logs": command_logs,
        "config": command_config,
        "env": command_env,
        "doctor": command_doctor,
        "cifereca": command_cifereca,
        "exec": command_exec,
        "snapshot": command_snapshot,
    }
    try:
        return int(handlers[args.command_name](args, catalog))
    except DatasetError as exc:
        print(f"datasetctl: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("datasetctl: interrompu", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
