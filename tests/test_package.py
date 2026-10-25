"""Verify release archives, permissions, platform completeness and version checks."""

import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("package", ROOT / "scripts/package.py")
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackageTests(unittest.TestCase):
    """Exercise packaging independently of native compiler availability."""

    def setUp(self):
        """Create a disposable project and one executable per release platform."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("plugin.json", "Cargo.toml", "LICENSE"):
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        self.binaries = self.root / "binaries"
        for platform, name in PACKAGE.PLATFORMS.items():
            target = self.binaries / platform / name
            target.parent.mkdir(parents=True)
            target.write_bytes(f"example {platform} binary".encode())

    def test_release_is_reproducible_and_bundles_all_platforms(self):
        """A release contains all run paths and preserves Unix executable modes."""
        platforms = list(PACKAGE.PLATFORMS)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}):
            archive, entry = PACKAGE.package(self.root, self.binaries, platforms, "v0.1.0")
            first = archive.read_bytes()
            PACKAGE.package(self.root, self.binaries, platforms, "v0.1.0")
            self.assertEqual(first, archive.read_bytes())
            registry = json.loads(entry.read_text())
        self.assertIn("owner/example/releases/download/v0.1.0/", registry["source"]["url"])
        with zipfile.ZipFile(archive) as zipped:
            manifest = json.loads(zipped.read("plugin.json"))
            self.assertEqual(set(manifest["run"]), set(platforms))
            for argv in manifest["run"].values():
                self.assertEqual(zipped.getinfo(argv[0]).external_attr >> 16, 0o100755)

    def test_local_package_only_advertises_available_platform(self):
        """A development package does not claim binaries for other systems."""
        archive, _ = PACKAGE.package(self.root, self.binaries, ["linux"])
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(json.loads(zipped.read("plugin.json"))["run"], {"linux": ["bin/linux/plugin"]})
            self.assertNotIn("bin/windows/plugin.exe", zipped.namelist())

    def test_missing_platform_binary_is_rejected(self):
        """An incomplete release fails instead of silently publishing one platform."""
        (self.binaries / "windows/plugin.exe").unlink()
        with self.assertRaisesRegex(ValueError, "missing or empty windows"):
            PACKAGE.package(self.root, self.binaries, list(PACKAGE.PLATFORMS))

    def test_tag_and_cargo_versions_must_match_manifest(self):
        """A release cannot publish a mismatched tag or Cargo package version."""
        with self.assertRaisesRegex(ValueError, "release tag"):
            PACKAGE.package(self.root, self.binaries, ["linux"], "v9.9.9")
        cargo = self.root / "Cargo.toml"
        cargo.write_text(cargo.read_text().replace('version = "0.1.0"', 'version = "0.2.0"'))
        with self.assertRaisesRegex(ValueError, "versions must match"):
            PACKAGE.package(self.root, self.binaries, ["linux"])


if __name__ == "__main__":
    unittest.main()
