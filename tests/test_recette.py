"""#3607: synthetic production only; no credentials or external providers."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/recette.py"
spec = importlib.util.spec_from_file_location("recette", SCRIPT)
recette = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recette)


class RecipeFixture(unittest.TestCase):
    def setUp(self):
        self.previous_umask = os.umask(0o077)
        # Pure policy tests use synthetic links. Real no-atime readlink mounts
        # are exercised separately in ActualSandboxTests and mandatory in CI.
        self.real_readlink_noatime = recette.readlink_noatime
        self.readlink_patch = patch.object(recette, "readlink_noatime",
                                           side_effect=lambda fd, name: os.readlink(name, dir_fd=fd))
        self.readlink_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.production = self.base / "production"
        self.production.mkdir()
        self.root = self.base / "cifereca-recettes"
        self.root.mkdir(mode=0o700)
        self.home_patch = patch.object(recette, "recipe_root", return_value=self.root)
        self.home_patch.start()
        self.target = self.root / "test"
        self.target.mkdir(mode=0o700)
        self.copier = recette.Copier(self.target)
        self.configdir = self.production / "config/cifereca"
        self.configdir.mkdir(parents=True)
        self.data = self.production / "data/cifereca"
        self.data.mkdir(parents=True)
        self.books = self.production / "books"
        self.books.mkdir()
        self.book = self.books / "a.epub"
        self.book.write_bytes(b"synthetic ebook bytes")
        self.config = self.configdir / "config.json"
        self.config.write_text(json.dumps({
            "library": {"directory": str(self.books)},
            "identities": [{"id": "alice", "adept_profile": "adept-alice"}, {"id": "bob"}],
            "providers": {"accounts": [{"id": "a", "identity_id": "alice", "account_id": "/opaque-account", "enabled": True,
                                        "credentials": {"password": "/synthetic-password", "provider_specific": "/synthetic-provider-secret", "access_token": "synthetic-session"}}]},
            "downloads": {"directory": str(self.books / "downloads"), "command": ["dangerous-hook"]},
        }))
        self.env_patch = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.production / "data")})
        self.env_patch.start()

    def tearDown(self):
        self.readlink_patch.stop()
        self.env_patch.stop()
        self.home_patch.stop()
        self.temporary.cleanup()
        os.umask(self.previous_umask)

    def fake_snapshot(self, target, ref):
        (target / "code/src/plugins").mkdir(parents=True)
        (target / "code/pyproject.toml").write_text('[project]\nname="fixture"\nversion="1"\n')
        return "a" * 40

    def fake_install(self, target):
        (target / "venv/bin").mkdir(parents=True)
        (target / "venv/bin/python").write_text("synthetic executable fixture")

    def create_fixture(self, *, root=None, target=None):
        target = target or self.target
        if target.exists():
            target.rmdir()
        with patch.object(recette, "export_snapshot", side_effect=self.fake_snapshot), patch.object(recette, "install", side_effect=self.fake_install):
            return recette.create(self.config, target, "main", no_start=True, root=root)

    def test_live_wal_copy_is_coherent_and_never_changes_production(self):
        source = self.data / "library.sqlite3"
        connection = sqlite3.connect(source)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT, details TEXT)")
        connection.execute("INSERT INTO books VALUES (1, ?, ?)", (str(self.book), json.dumps({"local_path": str(self.book)})))
        connection.commit()
        paths = [source, Path(str(source) + "-wal"), Path(str(source) + "-shm")]
        before = {str(p): (recette.fingerprint(p), p.stat().st_atime_ns) for p in paths}
        destination = self.target / "data/library.sqlite3"
        self.copier.backup(source, destination)
        with sqlite3.connect(destination) as copied:
            self.assertEqual(copied.execute("SELECT path FROM books").fetchone()[0], str(self.book))
            self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(before, {str(p): (recette.fingerprint(p), p.stat().st_atime_ns) for p in paths})

    def test_sql_paths_nested_json_uri_and_external_ebooks_are_copied(self):
        source = self.data / "library.sqlite3"
        outside = self.production / "external.epub"
        outside.write_bytes(b"another independent file")
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE placement (identity TEXT PRIMARY KEY, local_path TEXT, metadata TEXT) WITHOUT ROWID")
            db.executemany("INSERT INTO placement VALUES (?, ?, ?)", [
                ("alice", str(self.book), json.dumps({"file": str(outside), "relative_path": "a.epub"})),
                ("bob", "file://" + str(self.book), json.dumps({"local_path": "~/missing-fixture.epub"})),
            ])
        self.copier.register(self.books, self.target / "books")
        self.copier.capture(self.books, self.target / "books")
        destination = self.target / "library.sqlite3"
        self.copier.backup(source, destination)
        self.copier.rewrite_database(destination)
        with sqlite3.connect(destination) as db:
            rows = db.execute("SELECT * FROM placement ORDER BY identity").fetchall()
        self.assertEqual(rows[0][1], str(self.target / "books/a.epub"))
        self.assertEqual(rows[1][1], "file://" + str(self.target / "books/a.epub"))
        copied_outside = Path(json.loads(rows[0][2])["file"])
        self.assertTrue(copied_outside.is_relative_to(self.target))
        self.assertEqual(copied_outside.read_bytes(), outside.read_bytes())
        self.assertEqual(json.loads(rows[0][2])["relative_path"], "a.epub")
        self.assertNotEqual(copied_outside.stat().st_ino, outside.stat().st_ino)
        self.assertEqual([r[0] for r in rows], ["alice", "bob"])

    def test_existing_v14_and_legacy_migrations_affect_only_copy(self):
        # Real Core migrations, not a replacement migration implementation.
        from cifereca.storage import Storage, CURRENT_SCHEMA_VERSION, BASE_SCHEMA
        for legacy in (True, False):
            source = self.data / ("legacy.sqlite3" if legacy else "v14.sqlite3")
            if legacy:
                with sqlite3.connect(source) as db:
                    db.executescript(BASE_SCHEMA)
                    db.execute("INSERT INTO backup_items(provider_id,credential_id,item_id,title,local_path,status,updated_at) VALUES('provider','alice','book','Book',?,'complete',1)", (str(self.book),))
            else:
                storage = Storage(source)
                storage.close()
            before = recette.fingerprint(source)
            destination = self.target / source.name
            self.copier.backup(source, destination)
            self.copier.register(self.books, self.target / "books")
            self.copier.rewrite_database(destination)
            migrated = Storage(destination)
            self.assertEqual(migrated.schema_version(), CURRENT_SCHEMA_VERSION)
            migrated.close()
            self.assertEqual(recette.fingerprint(source), before)

    def test_primary_toml_and_json_are_read_without_chmod_or_atime(self):
        for filename, content in (("config.toml", '[library]\ndirectory="~/Books"\n'), ("config.json", '{"library":{"directory":"~/Books"}}')):
            path = self.production / filename
            path.write_text(content)
            path.chmod(0o640)
            before = path.stat()
            self.assertEqual(recette.load_config(path)["library"]["directory"], "~/Books")
            after = path.stat()
            self.assertEqual((before.st_mode, before.st_atime_ns, before.st_mtime_ns, before.st_ctime_ns),
                             (after.st_mode, after.st_atime_ns, after.st_mtime_ns, after.st_ctime_ns))

    def test_no_links_special_files_or_sessions_are_copied(self):
        alias = self.production / "alias"
        alias.symlink_to(self.book)
        with self.assertRaises(recette.Rejected):
            self.copier.capture(alias, self.target / "alias")
        fifo = self.production / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(recette.Rejected):
            self.copier.capture(fifo, self.target / "fifo")
        session = self.production / "browser"
        session.mkdir()
        (session / "cookies.json").write_text('{"token":"synthetic"}')
        self.copier.capture(session, self.target / "browser")
        self.assertFalse((self.target / "browser").exists())

    def test_full_preparation_manifest_config_guards_and_destroy(self):
        before = recette.fingerprint(self.config)
        target = self.create_fixture()
        config = json.loads((target / "config/cifereca/config.json").read_text())
        account = config["providers"]["accounts"][0]
        self.assertFalse(account["enabled"])
        self.assertEqual(account["credentials"], {"password": "/synthetic-password", "provider_specific": "/synthetic-provider-secret"})
        self.assertEqual(account["account_id"], "/opaque-account")
        self.assertEqual(config["downloads"]["command"], [])
        self.assertFalse(config["kobo_backup"]["enabled"])
        self.assertFalse(config["llm"]["enabled"])
        self.assertEqual(config["identities"], [{"id": "alice", "adept_profile": "adept-alice"}, {"id": "bob"}])
        self.assertEqual(recette.fingerprint(self.config), before)
        manifest_text = (target / "manifest.json").read_text()
        self.assertNotIn("synthetic-password", manifest_text)
        self.assertNotIn("synthetic-provider-secret", manifest_text)
        self.assertNotIn("/opaque-account", json.loads(manifest_text)["path_map"])
        self.assertEqual(json.loads(manifest_text)["sha"], "a" * 40)
        self.assertTrue(recette.verify(target)["ok"])
        self.assertTrue(recette.verify(target)["ok"])
        for path in target.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        recette.destroy(target)
        self.assertFalse(target.exists())
        self.assertTrue(self.book.exists())

    def test_tampered_executable_hardlink_and_source_drift_block_verification(self):
        target = self.create_fixture()
        tool = target / "runtime-tool.py"
        payload = tool.read_bytes()
        tool.write_text("tampered")
        with self.assertRaisesRegex(recette.Rejected, "Executable inventory"):
            recette.verify(target)
        tool.write_bytes(payload)
        os.link(self.book, target / "link.epub")
        with self.assertRaisesRegex(recette.Rejected, "Hardlink|Non-private"):
            recette.verify(target)
        (target / "link.epub").unlink()
        self.config.write_text("{}")
        with self.assertRaisesRegex(recette.Rejected, "Production changed"):
            recette.verify(target)

    def test_modified_config_cannot_point_back_to_production(self):
        target = self.create_fixture()
        path = target / "config/cifereca/config.json"
        config = json.loads(path.read_text())
        config["library"]["directory"] = str(self.books)
        recette.write_json(path, config)
        with self.assertRaisesRegex(recette.Rejected, "Runtime path outside"):
            recette.verify(target)

    def test_sql_identifiers_and_credentials_resembling_paths_stay_opaque(self):
        source = self.data / "library.sqlite3"
        identity = str(self.books / "opaque-identity")
        secret = str(self.books / "opaque-password")
        credential = str(self.books / "opaque-credential")
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE fixture_identity (identity_id TEXT, metadata_json TEXT)")
            db.execute("INSERT INTO fixture_identity VALUES (?, ?)", (identity, json.dumps({
                "password": secret, "credential_id": credential, "local_path": str(self.book),
                "credentials": {"provider_specific": secret},
            })))
        target = self.create_fixture()
        with sqlite3.connect(target / "data/cifereca/library.sqlite3") as db:
            row = db.execute("SELECT * FROM fixture_identity").fetchone()
        self.assertEqual(row[0], identity)
        metadata = json.loads(row[1])
        self.assertEqual(metadata["password"], secret)
        self.assertEqual(metadata["credential_id"], credential)
        self.assertEqual(metadata["credentials"]["provider_specific"], secret)
        self.assertEqual(metadata["local_path"], str(target / "library/a.epub"))
        self.assertTrue(recette.verify(target)["ok"])
        manifest = (target / "manifest.json").read_text()
        self.assertNotIn("opaque-password", manifest)
        self.assertNotIn("opaque-credential", manifest)

    def test_destroy_refuses_root_outside_symlink_and_forged_marker(self):
        for path in (self.root, self.production, self.base):
            with self.assertRaises(recette.Rejected):
                recette.destroy(path)
        alias = self.root / "alias"
        alias.symlink_to(self.production)
        with self.assertRaises(recette.Rejected):
            recette.destroy(alias)
        recette.write_json(self.target / "recipe.json", {"format": recette.FORMAT, "target": str(self.production)})
        with self.assertRaises(recette.Rejected):
            recette.destroy(self.target)
        self.assertTrue(self.book.exists())

    def test_failed_creation_is_destroyable_and_existing_target_never_overwritten(self):
        self.target.rmdir()
        with patch.object(recette, "export_snapshot", side_effect=recette.Rejected("interrupted")):
            with self.assertRaisesRegex(recette.Rejected, "interrupted"):
                recette.create(self.config, self.target, "main", no_start=True)
        self.assertEqual(json.loads((self.target / "recipe.json").read_text())["state"], "failed")
        with self.assertRaises(FileExistsError):
            recette.create(self.config, self.target, "main", no_start=True)
        recette.destroy(self.target)
        self.assertFalse(self.target.exists())

    def test_destroy_cannot_race_with_active_preparation(self):
        self.target.rmdir()
        def snapshot(target, ref):
            with self.assertRaisesRegex(recette.Rejected, "Supervisor still running"):
                recette.destroy(target)
            self.assertTrue(target.exists())
            return self.fake_snapshot(target, ref)
        with patch.object(recette, "export_snapshot", side_effect=snapshot), patch.object(recette, "install", side_effect=self.fake_install):
            recette.create(self.config, self.target, "main", no_start=True)
        self.assertTrue(recette.verify(self.target)["ok"])
        recette.destroy(self.target)
        self.assertFalse(self.target.exists())

    def test_sandbox_command_has_no_host_bus_proxy_home_or_network(self):
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            command = recette.sandbox(self.target, ["/usr/bin/true"])
        self.assertIn("--unshare-all", command)
        self.assertIn("--clearenv", command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("/run/user", command)
        self.assertNotIn(str(self.production), command)
        self.assertIn(str(self.target / "home"), command)
        self.assertIn("--cap-drop", command)

    def test_unavailable_sandbox_never_runs_application_unconfined(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(recette.Rejected, "no unconfined fallback"):
                recette.sandbox(self.target, ["/usr/bin/true"])

    def test_unstable_sqlite_refuses_instead_of_publishing_partial_copy(self):
        source = self.data / "library.sqlite3"
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE book (id INTEGER)")
        real_fingerprint = recette.fingerprint
        calls = 0
        def moving(path):
            nonlocal calls
            calls += 1
            return {**real_fingerprint(path), "mtime_ns": calls}
        with patch.object(recette, "fingerprint", side_effect=moving):
            with self.assertRaisesRegex(recette.Rejected, "did not stabilize"):
                self.copier.backup(source, self.target / "copy.sqlite3")
        self.assertFalse((self.target / "copy.sqlite3").exists())
        self.assertFalse((self.target / ".sqlite-stage").exists())

    def test_nested_library_has_one_mapping_and_independent_bytes(self):
        nested = self.data / "ebooks"
        nested.mkdir()
        (nested / "book.epub").write_bytes(b"nested book")
        self.copier.register(self.data, self.target / "data")
        self.copier.register(nested, self.target / "library")
        self.copier.capture(self.data, self.target / "data")
        self.copier.capture(nested, self.target / "library")
        self.assertEqual((self.target / "library/book.epub").read_bytes(), b"nested book")
        self.assertFalse((self.target / "data/ebooks").exists())

    def test_book_folders_named_browser_are_copied_as_content(self):
        folder = self.books / "browser"
        folder.mkdir()
        (folder / "title.epub").write_bytes(b"book, not a browser session")
        self.copier.register(self.books, self.target / "library")
        self.copier.content_roots.add(self.books)
        self.copier.capture(self.books, self.target / "library")
        self.assertEqual((self.target / "library/browser/title.epub").read_bytes(), b"book, not a browser session")

    def test_dotdot_paths_cannot_escape_copy_destination(self):
        self.copier.register(self.books, self.target / "books")
        external = self.production / "external.epub"
        external.write_bytes(b"outside library")
        result = Path(self.copier.rewrite(str(self.books / "../external.epub")))
        self.assertTrue(result.is_relative_to(self.target))
        self.assertNotIn("..", result.parts)
        self.assertEqual(result.read_bytes(), b"outside library")
        with self.assertRaisesRegex(recette.Rejected, "destination outside"):
            self.copier.capture(self.config, self.target / "../escape")

    def test_real_git_snapshot_is_pinned_and_symlink_archives_are_refused(self):
        repository = self.production / "git-fixture"
        subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, stdout=subprocess.DEVNULL)
        def git(*arguments):
            return subprocess.check_output(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *arguments], text=True).strip()
        (repository / "snapshot.txt").write_text("first commit")
        git("add", ".")
        git("commit", "-m", "synthetic first snapshot")
        first = git("rev-parse", "HEAD")
        actual_run, actual_output = subprocess.run, subprocess.check_output
        def network_run(command, **kwargs):
            command = list(command)
            if "fetch" in command:
                command[command.index("https://fixture.invalid/repo.git")] = str(repository)
            return actual_run(command, **kwargs)
        def output(command, **kwargs):
            if command[-3:] == ["remote", "get-url", "origin"]:
                return "https://fixture.invalid/repo.git\n"
            return actual_output(command, **kwargs)
        # Only transport routing is replaced; Git fetch/rev-parse/archive and
        # archive extraction execute for real against an isolated fixture.
        with patch("subprocess.run", side_effect=network_run), patch("subprocess.check_output", side_effect=output):
            sha = recette.export_snapshot(self.target, "main")
            self.assertEqual(sha, first)
            self.assertEqual((self.target / "code/snapshot.txt").read_text(), "first commit")
            self.assertFalse((self.target / "snapshot.git").exists())
        (repository / "snapshot.txt").write_text("second commit")
        git("add", ".")
        git("commit", "-m", "synthetic newer remote main")
        latest = git("rev-parse", "HEAD")
        fresh_target = self.root / "fresh-main"
        fresh_target.mkdir(mode=0o700)
        with patch("subprocess.run", side_effect=network_run), patch("subprocess.check_output", side_effect=output):
            self.assertEqual(recette.export_snapshot(fresh_target, "main"), latest)
            self.assertNotEqual(latest, first)
            self.assertEqual((fresh_target / "code/snapshot.txt").read_text(), "second commit")
        (repository / "alias").symlink_to(self.config)
        git("add", ".")
        git("commit", "-m", "synthetic unsafe snapshot")
        second_target = self.root / "second"
        second_target.mkdir(mode=0o700)
        with patch("subprocess.run", side_effect=network_run), patch("subprocess.check_output", side_effect=output):
            self.assertEqual(recette.export_snapshot(second_target, first), first)
            self.assertEqual((second_target / "code/snapshot.txt").read_text(), "first commit")
            unsafe_target = self.root / "unsafe"
            unsafe_target.mkdir(mode=0o700)
            with self.assertRaisesRegex(recette.Rejected, "Unsafe snapshot"):
                recette.export_snapshot(unsafe_target, "main")
        self.assertTrue(self.config.exists())
        with self.assertRaisesRegex(recette.Rejected, "full commit SHA"):
            recette.export_snapshot(self.target, "main; touch escaped")

    def test_stale_pid_is_never_signalled(self):
        recette.write_json(self.target / "recipe.json", {"format": recette.FORMAT, "id": "fixture", "target": str(self.target)})
        recette.write_json(self.target / "process.json", {"pid": os.getpid(), "starttime": "impossible", "id": "fixture"})
        with patch("os.killpg") as kill:
            recette.stop(self.target)
        kill.assert_not_called()

    def test_concurrent_launch_lock_blocks_destroy(self):
        import fcntl
        recette.write_json(self.target / "recipe.json", {"format": recette.FORMAT, "id": "fixture", "target": str(self.target)})
        with (self.target / "run.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(recette.Rejected, "Supervisor still running"):
                recette.destroy(self.target)
        self.assertTrue(self.target.exists())


    def test_alternative_private_root_nested_target_and_recorded_cli_actions(self):
        root = self.base / "mounted volume" / "private-recettes"
        target = self.create_fixture(root=root, target=root / "suite" / "T1")
        marker = json.loads((target / "recipe.json").read_text())
        self.assertEqual(marker["recipe_root"], str(root))
        self.assertEqual(recette.checked_target(target)[0], target)
        with patch.object(recette, "recipe_root", side_effect=AssertionError("default root must not be consulted")):
            self.assertTrue(recette.verify(target)["ok"])
            self.assertEqual(recette.main(["verify", str(target), "--recipe-root", str(root)]), 0)
            recette.stop(target)
            recette.destroy(target)
        self.assertFalse(target.exists())
        self.assertTrue(self.book.is_file())

    def test_alternative_root_cli_create_verify_and_destroy(self):
        root = self.base / "mounted" / "approved"
        target = root / "T1"
        with patch.object(recette, "export_snapshot", side_effect=self.fake_snapshot), patch.object(recette, "install", side_effect=self.fake_install):
            self.assertEqual(recette.main(["--config", str(self.config), "--recipe-root", str(root),
                                           "--target", str(target), "--ref", "main", "--no-start"]), 0)
        self.assertEqual(recette.main(["verify", str(target)]), 0)
        self.assertEqual(recette.main(["destroy", str(target), "--recipe-root", str(root)]), 0)
        self.assertFalse(target.exists())

    def test_root_permissions_traversal_mismatch_and_production_overlap_refused(self):
        for mode in (0o755, 0o777):
            root = self.base / ("permissions-" + oct(mode))
            root.mkdir(mode=mode)
            root.chmod(mode)
            with self.assertRaises(recette.Rejected):
                recette.target_path(root / "T1", root=root)
            self.assertEqual(root.stat().st_mode & 0o777, mode)
        root = self.base / "approved"
        for target in (self.base / "outside", root, root / "nested" / ".." / "escape"):
            with self.assertRaises(recette.Rejected):
                recette.target_path(target, root=root)
        for overlapping in (self.books / "recipes", self.production):
            with self.assertRaisesRegex(recette.Rejected, "overlaps"):
                recette.create(self.config, overlapping / "T1", "main", root=overlapping, no_start=True)
        self.assertFalse((self.books / "recipes").exists())
        self.assertFalse((self.production / ".cifereca-recipe-root.json").exists())
        target = self.create_fixture(root=root, target=root / "T1")
        with self.assertRaisesRegex(recette.Rejected, "does not match"):
            recette.checked_target(target, root=self.base / "foreign")
        recette.destroy(target)

    def test_noexec_or_unsupported_filesystem_refused_before_production_copy(self):
        root = self.base / "noexec"
        before = recette.fingerprint(self.config)
        with patch.object(recette.subprocess, "run", side_effect=PermissionError("synthetic noexec")):
            with self.assertRaisesRegex(recette.Rejected, "filesystem must support"):
                recette.create(self.config, root / "T1", "main", root=root, no_start=True)
        self.assertFalse((root / "T1").exists())
        self.assertEqual(recette.fingerprint(self.config), before)

    def test_internal_absolute_relative_runtime_current_are_materialized_and_rewritten(self):
        for absolute in (False, True):
            with self.subTest(absolute=absolute):
                runtime = self.data / ("runtime-absolute" if absolute else "runtime")
                version = runtime / "versions" / "release"
                version.mkdir(parents=True)
                binary = version / "engine"
                binary.write_bytes(b"synthetic local runtime")
                alias = runtime / "current"
                alias.symlink_to(version if absolute else Path("versions/release"), target_is_directory=True)
                config = json.loads(self.config.read_text())
                config["native_tool"] = {"path": str(alias / "engine")}
                self.config.write_text(json.dumps(config))
                before = recette.fingerprint(binary)
                link_before = recette.Copier.link_state(alias)
                target = self.create_fixture()
                copied = target / "data/cifereca" / runtime.name / "current/engine"
                self.assertTrue(copied.is_file())
                self.assertFalse(copied.parent.is_symlink())
                self.assertEqual(copied.read_bytes(), b"synthetic local runtime")
                self.assertNotEqual(copied.stat().st_ino, binary.stat().st_ino)
                self.assertEqual(recette.fingerprint(binary), before)
                self.assertEqual(recette.Copier.link_state(alias), link_before)
                rewritten = json.loads((target / "config/cifereca/config.json").read_text())
                self.assertEqual(rewritten["native_tool"]["path"], str(copied))
                manifest = json.loads((target / "manifest.json").read_text())
                self.assertEqual(manifest["source_links"][str(alias)], link_before)
                self.assertTrue(recette.verify(target)["ok"])
                recette.destroy(target)

    def test_links_outside_roots_broken_cyclic_traversal_and_sessions_are_refused(self):
        external = self.production / "outside-secret"
        external.write_bytes(b"must not be copied")
        excluded = self.books / "browser"
        excluded.mkdir()
        (excluded / "cookies.json").write_text("not copied")
        alias = self.books / "0-alias"
        cases = [external, self.books / "missing", alias, Path("../books/a.epub"),
                 excluded / "cookies.json", self.books]
        for number, link in enumerate(cases):
            with self.subTest(link=str(link)):
                alias.symlink_to(link)
                copier = recette.Copier(self.target)
                copier.register(self.books, self.target / str(number))
                with self.assertRaises(recette.Rejected):
                    copier.capture(self.books, self.target / str(number))
                alias.unlink()
        self.assertEqual(external.read_bytes(), b"must not be copied")

    def test_link_between_two_explicit_roots_is_materialized_independently(self):
        other = self.data / "local-dependency"
        other.write_bytes(b"dependency")
        alias = self.books / "dependency"
        alias.symlink_to(other)
        self.copier.register(self.books, self.target / "library")
        self.copier.register(self.data, self.target / "data")
        self.copier.capture(self.books, self.target / "library")
        self.assertEqual((self.target / "library/dependency").read_bytes(), b"dependency")
        self.assertFalse((self.target / "library/dependency").is_symlink())

    def test_symlink_change_during_copy_or_after_preparation_blocks_admission(self):
        alias = self.books / "alias.epub"
        alias.symlink_to(self.book)
        other = self.books / "other.epub"
        other.write_bytes(b"other bytes")
        self.copier.register(self.books, self.target / "library")
        copy = recette.copy_private_source
        def changed(source, destination):
            result = copy(source, destination)
            alias.unlink()
            alias.symlink_to(other)
            return result
        with patch.object(recette, "copy_private_source", side_effect=changed):
            with self.assertRaisesRegex(recette.Rejected, "symbolic link changed"):
                self.copier.capture(alias, self.target / "library/alias.epub")
        # New independent recipe records the new link; subsequent drift is
        # detected before execution, even when both targets are internal.
        target = self.create_fixture(target=self.root / "prepared")
        alias.unlink()
        alias.symlink_to(self.book)
        with self.assertRaisesRegex(recette.Rejected, "symbolic link changed"):
            recette.verify(target)
        recette.destroy(target)

    def test_excluded_session_symlink_never_resolves_its_external_target(self):
        alias = self.data / "sessions"
        alias.symlink_to(self.production / "nonexistent-secret-directory")
        target = self.create_fixture()
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertIn(str(alias), manifest["excluded_sessions"])
        self.assertFalse((target / "data/cifereca/sessions").exists())
        self.assertNotIn(str(alias), manifest["source_links"])
        recette.destroy(target)

    def test_release_environments_are_excluded_before_following_python_or_secret_links(self):
        for layout in ("releases", "versions"):
            for env in (".venv", "venv", "env", ".virtualenv"):
                with self.subTest(layout=layout, env=env):
                    version = self.data / "runtime" / layout / "v1"
                    environment = version / env
                    (environment / "bin").mkdir(parents=True)
                    (environment / "bin/python3.12").symlink_to("/usr/bin/python3.12")
                    (environment / "credentials").symlink_to(self.production / "missing-secret")
                    (version / "engine").write_bytes(b"runtime data")
                    current = self.data / "runtime/current"
                    current.symlink_to(f"{layout}/v1")
                    before = recette.Copier.link_state(current)
                    target = self.create_fixture()
                    manifest = json.loads((target / "manifest.json").read_text())
                    for relative in (f"runtime/{layout}/v1", "runtime/current"):
                        self.assertFalse((target / "data/cifereca" / relative / env).exists())
                        self.assertEqual((target / "data/cifereca" / relative / "engine").read_bytes(), b"runtime data")
                        self.assertIn(str(self.data / relative / env), manifest["excluded_rebuildable_runtimes"])
                    self.assertNotIn(str(environment / "bin/python3.12"), manifest["source_links"])
                    self.assertEqual(recette.Copier.link_state(current), before)
                    self.assertTrue(recette.verify(target)["ok"])
                    recette.destroy(target)
                    current.unlink()
                    shutil.rmtree(self.data / "runtime")

    def test_environment_aliases_are_excluded_without_resolving_even_broken_targets(self):
        version = self.data / "runtime/releases/v1"
        version.mkdir(parents=True)
        (version / ".venv").symlink_to(self.production / "missing-secret")
        current = self.data / "runtime/current"
        current.symlink_to(version)
        target = self.create_fixture()
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertEqual(len(manifest["excluded_rebuildable_runtimes"]), 2)
        self.assertNotIn(str(version / ".venv"), manifest["source_links"])
        self.assertTrue(recette.verify(target)["ok"])
        recette.destroy(target)

    def test_nested_release_dot_venvs_are_excluded_without_hiding_sibling_data(self):
        for absolute in (False, True):
            with self.subTest(absolute=absolute):
                version = self.data / "runtime/releases/v1"
                nested = version / "tools/compiler/.venv"
                (nested / "bin").mkdir(parents=True)
                (nested / "bin/python").symlink_to("/usr/bin/python3")
                secret = nested / "credentials"
                secret.symlink_to(self.production / "missing-secret")
                secret_before = secret.lstat()
                (nested.parent / "data.txt").write_bytes(b"keep useful sibling")
                current = self.data / "runtime/current"
                current.symlink_to(version if absolute else Path("releases/v1"))
                target = self.create_fixture()
                manifest = json.loads((target / "manifest.json").read_text())
                for prefix in ("runtime/current", "runtime/releases/v1"):
                    relative = Path(prefix) / "tools/compiler"
                    self.assertIn(str(self.data / relative / ".venv"), manifest["excluded_rebuildable_runtimes"])
                    self.assertFalse((target / "data/cifereca" / relative / ".venv").exists())
                    self.assertEqual((target / "data/cifereca" / relative / "data.txt").read_bytes(), b"keep useful sibling")
                self.assertEqual(secret.lstat(), secret_before)
                self.assertTrue(recette.verify(target)["ok"])
                recette.destroy(target)
                current.unlink()
                shutil.rmtree(self.data / "runtime")

    def test_runtime_exclusion_never_hides_external_links_in_books_or_useful_data(self):
        for source in (self.books / "runtime/releases/v1/.venv",
                       self.data / "runtime/releases/v1/business-data"):
            with self.subTest(source=str(source)):
                source.mkdir(parents=True)
                (source / "secret").symlink_to(self.production / "outside-secret")
                with self.assertRaisesRegex(recette.Rejected, "External source symbolic link"):
                    self.create_fixture()
                recette.destroy(self.target)
                shutil.rmtree(source)

    def test_alias_into_excluded_environment_is_refused(self):
        environment = self.data / "runtime/releases/v1/.venv"
        environment.mkdir(parents=True)
        (self.data / "useful-dependency").symlink_to(environment)
        with self.assertRaisesRegex(recette.Rejected, "targets excluded runtime"):
            self.create_fixture()

    def test_destination_symlinks_and_hardlinks_never_modify_source(self):
        before = self.book.stat()
        alias = self.target / "alias"
        alias.symlink_to(self.book)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.copy_private_source(self.config, alias)
        alias.unlink()
        os.link(self.book, alias)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.copy_private_source(self.config, alias)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.seal_permissions(self.target)
        alias.unlink()
        parent = self.target / "parent"
        parent.symlink_to(self.production, target_is_directory=True)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.copy_private_source(self.book, parent / "overwrite")
        self.assertEqual(self.book.read_bytes(), b"synthetic ebook bytes")
        self.assertEqual(self.book.stat().st_mode, before.st_mode)
        self.assertFalse((self.production / "overwrite").exists())

    def test_destroy_unlinks_internal_hostile_link_without_following_it(self):
        target = self.create_fixture()
        (target / "hostile").symlink_to(self.production, target_is_directory=True)
        recette.destroy(target)
        self.assertFalse(target.exists())
        self.assertTrue(self.config.exists())
        self.assertTrue(self.book.exists())

    def test_source_ancestor_symlink_swap_is_refused_by_descriptor_walk(self):
        saved = self.production / "saved-books"
        outside = self.production / "foreign"
        outside.mkdir()
        foreign = outside / self.book.name
        foreign.write_bytes(b"outside")
        before = foreign.stat()
        check = recette.no_links
        swapped = False
        def swap(path):
            nonlocal swapped
            check(path)
            if path == self.book and not swapped:
                swapped = True
                self.books.rename(saved)
                self.books.symlink_to(outside, target_is_directory=True)
        try:
            with patch.object(recette, "no_links", side_effect=swap):
                with self.assertRaises((recette.Rejected, OSError)):
                    recette.read_private_source(self.book)
            self.assertEqual(foreign.stat().st_atime_ns, before.st_atime_ns)
        finally:
            if swapped:
                self.books.unlink()
                saved.rename(self.books)

    def test_sqlite_destination_link_cannot_overwrite_production(self):
        source = self.data / "source.sqlite"
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE original (value TEXT)")
            db.execute("INSERT INTO original VALUES ('source')")
        other = self.data / "untouched.sqlite"
        with sqlite3.connect(other) as db:
            db.execute("CREATE TABLE sentinel (value TEXT)")
        before = recette.fingerprint(other)
        destination = self.target / "copy.sqlite"
        destination.symlink_to(other)
        with self.assertRaises((recette.Rejected, OSError)):
            self.copier.backup(source, destination)
        self.assertEqual(recette.fingerprint(other), before)
        destination.unlink()
        os.link(other, destination)
        with self.assertRaises((recette.Rejected, OSError)):
            self.copier.backup(source, destination)
        self.assertEqual(recette.fingerprint(other)["sha256"], before["sha256"])

    def test_recipe_root_approval_and_target_identity_cannot_be_substituted(self):
        target = self.create_fixture()
        marker = json.loads((target / "recipe.json").read_text())
        marker["root_id"] = "f" * 32
        recette.write_json(target / "recipe.json", marker)
        with self.assertRaisesRegex(recette.Rejected, "approved recipe root"):
            recette.checked_target(target)
        marker["root_id"] = json.loads((self.root / ".cifereca-recipe-root.json").read_text())["id"]
        recette.write_json(target / "recipe.json", marker)
        foreign = self.production / "foreign-safe"
        foreign.mkdir()
        (foreign / "sentinel").write_text("unchanged")
        moved = self.root / "moved"
        def replaced(_target):
            target.rename(moved)
            target.symlink_to(foreign, target_is_directory=True)
        with patch.object(recette, "stop", side_effect=replaced):
            with self.assertRaises((recette.Rejected, OSError)):
                recette.destroy(target)
        self.assertTrue(moved.is_dir())
        self.assertEqual((foreign / "sentinel").read_text(), "unchanged")
        self.assertFalse((foreign / "run.lock").exists())
        target.unlink()

    def test_log_and_lock_symlinks_or_fifos_never_write_or_block(self):
        before = recette.fingerprint(self.config)
        log = self.target / "launcher.log"
        log.symlink_to(self.config)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.private_append(log)
        log.unlink()
        os.mkfifo(log)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.private_append(log)
        self.assertEqual(recette.fingerprint(self.config), before)

    def test_metadata_symlink_is_refused_before_reading_production(self):
        target = self.create_fixture()
        outside = self.production / "outside.json"
        outside.write_text('{"pid": 123, "id": "foreign"}')
        os.utime(outside, ns=(1, 1))
        before = outside.stat()
        manifest = target / "manifest.json"
        manifest.unlink()
        manifest.symlink_to(outside)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.verify(target)
        (target / "process.json").symlink_to(outside)
        with self.assertRaises((recette.Rejected, OSError)):
            recette.runtime_record(target)
        self.assertEqual(outside.stat().st_atime_ns, before.st_atime_ns)
        self.assertEqual(outside.stat().st_mtime_ns, before.st_mtime_ns)


class ActualSandboxTests(unittest.TestCase):
    """Actual OS boundary, mandatory in the scoped Linux CI job."""
    setUp = RecipeFixture.setUp
    tearDown = RecipeFixture.tearDown
    fake_snapshot = RecipeFixture.fake_snapshot
    create_fixture = RecipeFixture.create_fixture

    def fake_install(self, target):
        (target / "venv/bin").mkdir(parents=True)
        executable = target / "venv/bin/python"
        executable.write_text('#!/bin/sh\nexec /usr/bin/python3 -c "import time; time.sleep(120)"\n')
        executable.chmod(0o700)

    def require_sandbox(self):
        try:
            recette.probe(self.target)
        except (recette.Rejected, subprocess.SubprocessError):
            if os.environ.get("CIFERECA_REQUIRE_SANDBOX_TESTS") == "1":
                raise
            self.skipTest("Bubblewrap namespaces unavailable on this host")

    def test_real_venv_install_and_migration_in_sandbox(self):
        if os.environ.get("CIFERECA_REQUIRE_SANDBOX_TESTS") != "1":
            self.skipTest("Scoped CI installation acceptance only")
        self.require_sandbox()
        for name in ("home", "config", "data", "state", "cache", "runtime", "tmp", "code/cifereca"):
            (self.target / name).mkdir(parents=True, exist_ok=True)
        (self.target / "code/pyproject.toml").write_text('''[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"
[project]
name = "cifereca-recette-install-fixture"
version = "1.0"
dependencies = []
''')
        (self.target / "code/cifereca/__init__.py").write_text("")
        (self.target / "code/cifereca/storage.py").write_text('''import os, pathlib, sqlite3
class Storage:
    def __init__(self):
        path = pathlib.Path(os.environ['XDG_DATA_HOME']) / 'cifereca/library.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE migrated (version INTEGER)')
            db.execute('INSERT INTO migrated VALUES (14)')
    def schema_version(self): return 14
''')
        with socket.socket() as host_service:
            host_service.bind(("127.0.0.1", 0))
            host_service.listen()
            # Executed by real setuptools metadata/wheel builds. Local code
            # must never inherit the dependency worker's host networking.
            (self.target / "code/setup.py").write_text(
                "from setuptools import setup\nimport socket\n"
                "s=socket.socket(); s.settimeout(1)\n"
                f"assert s.connect_ex(('127.0.0.1', {host_service.getsockname()[1]})) != 0\n"
                "setup()\n"
            )
            recette.install(self.target)
        with sqlite3.connect(self.target / "data/cifereca/library.sqlite3") as db:
            self.assertEqual(db.execute("SELECT version FROM migrated").fetchone(), (14,))
        self.assertTrue((self.target / "requirements-installed.txt").exists())
        self.assertFalse((self.target / "venv/lib64").is_symlink())

    def _exercise_actual_lifecycle(self, *, alternate=False):
        self.require_sandbox()
        if not shutil.which("Xvfb") or not shutil.which("dbus-run-session"):
            if os.environ.get("CIFERECA_REQUIRE_SANDBOX_TESTS") == "1":
                self.fail("Xvfb and dbus-run-session required in acceptance CI")
            self.skipTest("Xvfb/dbus-run-session unavailable")
        import select
        read_fd, write_fd = os.pipe()
        with subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "800x600x24", "-nolisten", "tcp", "-ac"], pass_fds=(write_fd,)) as display:
            os.close(write_fd)
            try:
                self.assertTrue(select.select([read_fd], [], [], 10)[0], "Xvfb did not start")
                number = os.read(read_fd, 100).decode().strip()
                os.close(read_fd)
                root = self.base / "mounted-volume" / "recipes" if alternate else None
                target = self.create_fixture(root=root, target=(root / "nested/T1") if root else None)
                with patch.dict(os.environ, {"DISPLAY": ":" + number, "HOME": str(self.base), "XAUTHORITY": str(self.base / "no-auth")}):
                    for attempt in range(2):
                        recette.start(target)
                        deadline = time.monotonic() + 10
                        while not recette.runtime_record(target) and time.monotonic() < deadline:
                            time.sleep(0.05)
                        row = recette.runtime_record(target)
                        self.assertIsNotNone(row, (target / "launcher.log").read_text())
                        if attempt == 0:
                            # Abrupt sandbox death must be reaped by supervisor
                            # and leave a recipe that can be launched again.
                            os.killpg(row["pid"], 9)
                            deadline = time.monotonic() + 10
                            while (target / "process.json").exists() and time.monotonic() < deadline:
                                time.sleep(0.05)
                        else:
                            recette.destroy(target)
                self.assertFalse(target.exists())
                self.assertTrue(self.book.exists())
            finally:
                display.terminate()
    def test_actual_launch_stop_crash_restart_and_destroy(self):
        self._exercise_actual_lifecycle()

    def test_actual_alternative_root_launch_stop_crash_restart_and_destroy(self):
        self._exercise_actual_lifecycle(alternate=True)

    def test_actual_internal_link_copy_preserves_link_atime(self):
        self.require_sandbox()
        alias = self.books / "alias.epub"
        alias.symlink_to(self.book)
        os.utime(alias, ns=(1, 1), follow_symlinks=False)
        before = alias.lstat()
        with patch.object(recette, "readlink_noatime", self.real_readlink_noatime):
            self.copier = recette.Copier(self.target)
            self.copier.register(self.books, self.target / "library")
            self.copier.capture(self.books, self.target / "library")
        after = alias.lstat()
        self.assertEqual((before.st_atime_ns, before.st_mtime_ns, before.st_ctime_ns, before.st_ino),
                         (after.st_atime_ns, after.st_mtime_ns, after.st_ctime_ns, after.st_ino))
        self.assertEqual((self.target / "library/alias.epub").read_bytes(), b"synthetic ebook bytes")

    def test_real_mounted_alternative_root_create_verify_confine_destroy(self):
        self.require_sandbox()
        mounted = os.environ.get("CIFERECA_RECIPE_MOUNT_ROOT")
        if not mounted:
            if os.environ.get("CIFERECA_REQUIRE_SANDBOX_TESTS") == "1":
                self.fail("CI must supply a private mounted CIFERECA_RECIPE_MOUNT_ROOT")
            self.skipTest("Requires an administrator-provisioned isolated test mount")
        volume = Path(mounted)
        self.assertTrue(volume.is_absolute() and not volume.is_symlink() and os.path.ismount(volume))
        self.assertEqual(volume.stat().st_uid, os.getuid())
        self.assertEqual(volume.stat().st_mode & 0o777, 0o700)
        version = self.data / "runtime/releases/v1"
        version.mkdir(parents=True)
        (version / "engine").write_bytes(b"synthetic runtime")
        current = self.data / "runtime/current"
        current.symlink_to("releases/v1", target_is_directory=True)
        (version / ".venv/bin").mkdir(parents=True)
        (version / ".venv/bin/python3.12").symlink_to("/usr/bin/python3.12")
        os.utime(current, ns=(1, 1), follow_symlinks=False)
        link_before = current.lstat()
        database = self.data / "library.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, local_path TEXT)")
            db.execute("INSERT INTO books VALUES (1, ?)", (str(self.book),))
        database_before = recette.fingerprint(database)
        program = r"""
import importlib.util, json, os, pathlib, subprocess, sys
spec = importlib.util.spec_from_file_location('recette', sys.argv[1])
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
def snapshot(target, ref):
    (target / 'code/src/plugins').mkdir(parents=True)
    return 'a' * 40
def install(target):
    (target / 'venv/bin').mkdir(parents=True)
    (target / 'venv/bin/python').write_text('fixture')
r.export_snapshot = snapshot; r.install = install
root = pathlib.Path(sys.argv[3]) / 'cifereca-tests'
target = r.create(pathlib.Path(sys.argv[2]), root / 'T1', 'main', root=root, no_start=True)
assert r.verify(target)['ok']
assert (target / 'data/cifereca/runtime/current/engine').read_bytes() == b'synthetic runtime'
assert not (target / 'data/cifereca/runtime/current').is_symlink()
assert not (target / 'data/cifereca/runtime/current/.venv').exists()
assert not (target / 'data/cifereca/runtime/releases/v1/.venv').exists()
assert len(json.loads((target / 'manifest.json').read_text())['excluded_rebuildable_runtimes']) == 2
import sqlite3
with sqlite3.connect(target / 'data/cifereca/library.sqlite3') as db:
    assert db.execute('SELECT local_path FROM books').fetchone()[0] == str(target / 'library/a.epub')
r.probe(target)
subprocess.run(r.sandbox(target, ['/usr/bin/python3', '-c',
    'import pathlib,sys; assert not pathlib.Path(sys.argv[1]).exists(); '
    'pathlib.Path(sys.argv[2]).write_text("inside")', sys.argv[2], str(target / 'home/allowed')]), check=True)
assert (target / 'home/allowed').read_text() == 'inside'
r.destroy(target)
assert not target.exists()
"""
        before = recette.fingerprint(self.config)
        # Run the controller as the normal host user. Nested bwrap bootstrap
        # inside an already capability-stripped AppArmor child must fail;
        # the real recipe controller does not need that privilege.
        command = ["/usr/bin/python3", "-c", program, str(SCRIPT), str(self.config), str(volume)]
        subprocess.run(command, check=True, env=dict(os.environ, XDG_DATA_HOME=str(self.production / "data")), timeout=60)
        self.assertFalse((volume / "cifereca-tests/T1").exists())
        self.assertEqual(recette.fingerprint(self.config), before)
        self.assertEqual(recette.fingerprint(database), database_before)
        self.assertEqual(current.lstat().st_atime_ns, link_before.st_atime_ns)

    def test_subprocess_network_writes_and_host_sockets_are_confined(self):
        self.require_sandbox()
        for name in ("home", "config", "data", "state", "cache", "runtime", "tmp", "library", "imports", "code"):
            (self.target / name).mkdir()
        for name in ("config", "data", "library", "imports", "home", "state", "runtime", "tmp"):
            (self.target / name / "synthetic-secret").write_text("synthetic private copy")
        # This is the network-enabled build boundary, before the runtime
        # network denial assertions below. Private copies must be hidden even
        # though the code/venv/cache remain available to the installer.
        build_code = '''
from pathlib import Path
import sys
root = Path(sys.argv[1])
assert not Path('/etc/ssl/private').exists()
for name in ('config', 'data', 'library', 'imports', 'home', 'state', 'runtime', 'tmp'):
    assert not (root / name / 'synthetic-secret').exists(), name
    (root / name / 'temporary-build-file').write_text('ephemeral')
(root / 'code/build-output').write_text('persistent code output')
'''
        subprocess.run(recette.sandbox(self.target, ["/usr/bin/python3", "-c", build_code, str(self.target)], network=True), check=True)
        self.assertEqual((self.target / "code/build-output").read_text(), "persistent code output")
        for name in ("config", "data", "library", "imports", "home", "state", "runtime", "tmp"):
            self.assertEqual((self.target / name / "synthetic-secret").read_text(), "synthetic private copy")
            self.assertFalse((self.target / name / "temporary-build-file").exists())
        sentinel = self.production / "sentinel"
        sentinel.write_text("unchanged")
        unix_path = self.production / "host.sock"
        with socket.socket(socket.AF_UNIX) as unix, socket.socket() as server:
            unix.bind(str(unix_path))
            server.bind(("127.0.0.1", 0))
            server.listen()
            code = '''
import os, pathlib, socket, subprocess
source, host_socket, port = __import__('sys').argv[1:]
assert 'HTTP_PROXY' not in os.environ and 'SSH_AUTH_SOCK' not in os.environ
assert not pathlib.Path(source).exists()
assert not pathlib.Path(host_socket).exists()
for path in (source, '/usr/recette-forbidden', '/outside-recette'):
    try: pathlib.Path(path).write_text('BAD')
    except OSError: pass
    else: raise AssertionError('external write allowed')
s = socket.socket(); s.settimeout(1)
assert s.connect_ex(('127.0.0.1', int(port))) != 0
subprocess.run(['/bin/sh', '-c', 'test ! -e "$1" && ! echo BAD > "$1"', 'sh', source], check=True)
pathlib.Path(os.environ['HOME'], 'allowed').write_text('inside')
'''
            subprocess.run(recette.sandbox(self.target, ["/usr/bin/python3", "-c", code, str(sentinel), str(unix_path), str(server.getsockname()[1])]), check=True)
        self.assertEqual(sentinel.read_text(), "unchanged")
        self.assertEqual((self.target / "home/allowed").read_text(), "inside")


if __name__ == "__main__":
    unittest.main()
