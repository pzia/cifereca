from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import datasetctl


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class DatasetCtlSmokeTests(unittest.TestCase):
    def test_up_summary_and_down_with_small_opds_dataset(self):
        catalog = datasetctl.load_catalog()
        old_runtime = os.environ.get(datasetctl.RUNTIME_ENV)
        old_plugins = os.environ.get("CIFERECA_DATASET_PLUGIN_SOURCE")
        profile = {
            "name": "smoke-opds",
            "runtime": {"startup_timeout_seconds": 10},
            "cifereca": {"no_plugins": True},
            "library": {"epubs": 0, "seed": 42},
            "services": {
                "opds": {
                    "enabled": True,
                    "port": free_port(),
                    "books_per_source": 3,
                    "seed": 42,
                }
            },
        }
        try:
            with tempfile.TemporaryDirectory() as runtime, tempfile.TemporaryDirectory() as plugins:
                os.environ[datasetctl.RUNTIME_ENV] = runtime
                os.environ["CIFERECA_DATASET_PLUGIN_SOURCE"] = plugins

                prepared = datasetctl.start_services(profile, catalog, rebuild=True)
                self.assertTrue(prepared["runtime"].is_dir())

                rows = datasetctl.status_rows(profile, catalog)
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0]["process"])
                self.assertTrue(rows[0]["health"])

                summary = datasetctl.summary_payload(profile, catalog)
                self.assertTrue(summary["library"]["complete"])
                self.assertEqual(summary["library"]["generated_epubs"], 0)
                self.assertEqual(summary["services"][0]["reported"]["counts"], {
                    "gallica": 3,
                    "gutenberg": 3,
                })

                datasetctl.down(profile)
                rows = datasetctl.status_rows(profile, catalog)
                self.assertFalse(rows[0]["process"])
        finally:
            try:
                datasetctl.down(profile)
            except Exception:
                pass
            if old_runtime is None:
                os.environ.pop(datasetctl.RUNTIME_ENV, None)
            else:
                os.environ[datasetctl.RUNTIME_ENV] = old_runtime
            if old_plugins is None:
                os.environ.pop("CIFERECA_DATASET_PLUGIN_SOURCE", None)
            else:
                os.environ["CIFERECA_DATASET_PLUGIN_SOURCE"] = old_plugins


if __name__ == "__main__":
    unittest.main()
