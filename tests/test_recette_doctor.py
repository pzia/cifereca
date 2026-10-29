"""Deterministic diagnostic contract; real bootstrap is mandatory in recipe CI."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/recette-doctor.py"
spec = importlib.util.spec_from_file_location("recette_doctor", SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def diagnose(self, *, label="bwrap//&unpriv_bwrap (enforce)", restricted="1",
                 bootstrap=0, feature=True, child=None, profile=None, syntax=0,
                 capability=None, capability_exit=0):
        commands = []
        def run(args, **kwargs):
            commands.append((args, kwargs))
            output, status = "", 0
            if "--help" in args:
                output = "--ro-bind-fd" if feature else "--ro-bind"
            elif "--skip-kernel-load" in args:
                status = syntax
            elif "--unshare-all" in args:
                status = bootstrap
                if doctor.CAPABILITY_CHILD in args:
                    status = capability_exit
                    output = capability if capability is not None else json.dumps({
                        "apparmor_label": label, "status": "denied", "stage": "mount_namespace",
                        "errno": 1, "error": "Operation not permitted"})
                if doctor.CHILD in args:
                    output = child if child is not None else json.dumps({
                        "namespaces": {kind: "child:" + kind for kind in ("user", "net", "mnt", "pid")},
                        "apparmor_label": label, "cap_eff": "0", "no_new_privs": 1})
            return {"command": args, "returncode": status, "stdout": output, "stderr": "original denial" if status else ""}
        def read(path):
            if path.endswith("/enabled"):
                return "Y"
            if path.endswith("apparmor_restrict_unprivileged_userns"):
                return restricted
            return "synthetic host information"
        with patch.object(doctor, "command", side_effect=run), \
                patch.object(doctor, "read", side_effect=read), \
                patch.object(doctor.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(doctor.os, "getuid", return_value=1000), \
                patch.object(doctor.os, "readlink", side_effect=lambda path: "parent:" + str(path)):
            report = doctor.diagnose(require_apparmor=True, profile=profile)
        return report, commands

    def test_real_probe_commands_preserve_all_confinement_flags_and_readonly_mounts(self):
        report, commands = self.diagnose()
        self.assertTrue(report["ok"], report)
        probes = [args for args, _ in commands if "--unshare-all" in args]
        self.assertEqual(len(probes), 3)
        for args in probes:
            for flag in ("--cap-drop", "ALL", "--clearenv", "--remount-ro", "--new-session"):
                self.assertIn(flag, args)
            self.assertNotIn("--share-net", args)
            self.assertNotIn("/home", args)
        self.assertTrue(any("--ro-bind-fd" in args and kwargs["pass_fds"] for args, kwargs in commands))
        self.assertIn("not_recipe_or_host_acceptance", report["scope"])

    def test_bootstrap_denial_keeps_original_error_and_fails(self):
        report, _ = self.diagnose(bootstrap=1)
        self.assertFalse(report["ok"])
        self.assertIn("bubblewrap_bootstrap_failed", report["failures"])
        self.assertEqual(report["checks"]["bootstrap"]["stderr"], "original denial")

    def test_old_bubblewrap_is_explicit_failure_without_symlink_fallback(self):
        report, commands = self.diagnose(feature=False)
        self.assertIn("ro_bind_fd_unavailable_for_source_symlinks", report["failures"])
        self.assertEqual(sum("--unshare-all" in args for args, _ in commands), 2)

    def test_permission_denial_is_required_in_child_even_when_initial_capabilities_are_zero(self):
        result = {"apparmor_label": "bwrap//&unpriv_bwrap (enforce)",
                  "status": "allowed", "stage": "mount_namespace", "errno": 0}
        report, _ = self.diagnose(capability=json.dumps(result))
        self.assertFalse(report["ok"])
        self.assertIn("child_capability_denial_unverified", report["failures"])
        self.assertEqual(report["checks"]["child"]["cap_eff"], "0")
        self.assertEqual(report["checks"]["child_capability_result"], result)

    def test_child_permission_denials_preserve_stage_errno_and_cause(self):
        for stage in ("user_namespace", "mount_namespace"):
            for code in (1, 13):
                result = {"apparmor_label": "bwrap//&unpriv_bwrap (enforce)",
                          "status": "denied", "stage": stage, "errno": code,
                          "error": "original permission error"}
                with self.subTest(stage=stage, code=code):
                    report, _ = self.diagnose(capability=json.dumps(result))
                    self.assertTrue(report["ok"], report)
                    self.assertEqual(report["checks"]["child_capability_result"], result)

    def test_probe_errors_limits_or_invalid_results_are_not_permission_denials(self):
        base = {"apparmor_label": "bwrap//&unpriv_bwrap (enforce)",
                "status": "denied", "stage": "mount_namespace", "errno": 1}
        cases = ["not json", "null", "[]", "{}"] + [json.dumps({**base, **change}) for change in (
            {"errno": 38}, {"errno": 22}, {"errno": 28}, {"errno": True},
            {"status": "unverified"}, {"stage": "unknown"}, {"apparmor_label": "unconfined"})]
        for value in cases:
            with self.subTest(value=value):
                report, _ = self.diagnose(capability=value)
                self.assertIn("child_capability_denial_unverified", report["failures"])
        report, _ = self.diagnose(capability_exit=1)
        self.assertIn("child_capability_denial_unverified", report["failures"])

    def test_failed_bootstrap_does_not_run_or_accept_child_probe(self):
        report, commands = self.diagnose(bootstrap=1)
        self.assertNotIn("child_capability_probe", report["checks"])
        self.assertFalse(any(doctor.CAPABILITY_CHILD in args for args, _ in commands))

    def test_unconfined_complain_or_disabled_global_policy_never_pass(self):
        for label, restricted in (("unconfined", "1"), ("bwrap (enforce)", "1"),
                                  ("bwrap//&unpriv_bwrap (complain)", "1"),
                                  ("bwrap//&unpriv_bwrap (enforce)", "0")):
            with self.subTest(label=label, restricted=restricted):
                report, _ = self.diagnose(label=label, restricted=restricted)
                self.assertFalse(report["ok"])

    def test_missing_parent_namespace_is_reported_without_crashing(self):
        with patch.object(doctor.os, "readlink", side_effect=FileNotFoundError("restricted proc")), \
                patch.object(doctor.shutil, "which", return_value="/usr/bin/bwrap"), \
                patch.object(doctor, "command", return_value={"returncode": 1, "stdout": "", "stderr": "denied"}):
            report = doctor.diagnose()
        self.assertIn("parent_namespaces_unavailable", report["failures"])
        self.assertIn("restricted proc", report["namespace_error"])
        self.assertIn("bubblewrap_features_unverified", report["failures"])
        self.assertNotIn("ro_bind_fd_unavailable_for_source_symlinks", report["failures"])
        self.assertEqual(report["checks"]["bwrap_help"]["stderr"], "denied")

    def test_profile_validation_is_syntax_only_without_cache_or_kernel_write(self):
        report, commands = self.diagnose(profile=Path("/test/profile"))
        self.assertTrue(report["ok"])
        syntax = next(args for args, _ in commands if "--skip-kernel-load" in args)
        self.assertIn("--skip-cache", syntax)
        self.assertNotIn("--replace", syntax)
        self.assertFalse(any(args[0] in ("sudo", "sysctl", "mount", "aa-disable") for args, _ in commands))
        report, _ = self.diagnose(profile=Path("/test/profile"), syntax=1)
        self.assertIn("profile_syntax_unverified", report["failures"])

    def test_invalid_bootstrap_evidence_never_passes(self):
        for value in ("not json", "{}", "null"):
            with self.subTest(value=value):
                report, _ = self.diagnose(child=value)
                self.assertIn("invalid_bootstrap_evidence", report["failures"])

    def test_missing_binary_or_timeout_is_structured_and_preserves_cause(self):
        for exc in (FileNotFoundError("missing tool"), subprocess.TimeoutExpired(["bwrap"], 20)):
            with self.subTest(exc=type(exc).__name__), patch.object(doctor.subprocess, "run", side_effect=exc):
                result = doctor.command(["bwrap", "--version"])
                self.assertIsNone(result["returncode"])
                self.assertIn(str(exc), result["error"])


if __name__ == "__main__":
    unittest.main()
