"""Smoke tests for the installer's and service's different Python package roots."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


class InstallerImportTests(unittest.TestCase):
    """Import entry point dependencies without starting installation or services."""

    def _CheckImports(self, workingDirectory: Path, code: str) -> None:
        env = os.environ.copy()
        # Match each launcher's package root, regardless of the test runner's path.
        env["PYTHONPATH"] = str(workingDirectory)
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=workingDirectory,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(importlib.util.find_spec("pwd"), "Installer requires Unix")
    def test_installer_imports_from_repository_root(self) -> None:
        """Install and update must load before any installer actions can run."""
        self._CheckImports(
            Path(__file__).resolve().parents[1],
            "from homeway_installer.Installer import Installer",
        )

    def test_service_imports_from_addon_root(self) -> None:
        """The service uses the inner homeway directory as its package root."""
        self._CheckImports(
            Path(__file__).resolve().parents[1] / "homeway",
            "from homeway.interfaces import IWebStreamHelper; "
            "from homeway.websocketimpl import Client; "
            "from homeway.Proto.WebStreamMsg import WebStreamMsg",
        )


if __name__ == "__main__":
    unittest.main()
