"""Verify the per-platform release archives, permissions, completeness and version checks."""

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

    def test_release_is_reproducible_with_a_package_per_platform(self):
        """A release has one package per platform, each with only its own executable."""
        platforms = list(PACKAGE.PLATFORMS)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}):
            archives, entry = PACKAGE.package(self.root, self.binaries, platforms, "v0.1.0")
            first = [a.read_bytes() for a in archives]
            PACKAGE.package(self.root, self.binaries, platforms, "v0.1.0")
            self.assertEqual(first, [a.read_bytes() for a in archives])
            registry = json.loads(entry.read_text())
        self.assertEqual(set(registry["source"]), set(platforms))
        shared = []
        for platform, archive in zip(platforms, archives):
            self.assertEqual(archive.name, f"my-plugin-0.1.0-{platform}.rgplugin")
            source = registry["source"][platform]
            self.assertEqual(source["url"], f"https://github.com/owner/example/releases/download/v0.1.0/{archive.name}")
            self.assertEqual(source["sha256"], PACKAGE.hashlib.sha256(archive.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive) as zipped:
                manifest = json.loads(zipped.read("plugin.json"))
                program = f"bin/{platform}/{PACKAGE.PLATFORMS[platform]}"
                self.assertEqual(manifest.pop("run"), {platform: [program]})
                self.assertEqual(sorted(zipped.namelist()), sorted(["LICENSE", "plugin.json", program]))
                self.assertEqual(zipped.getinfo(program).external_attr >> 16, 0o100755)
                self.assertEqual(zipped.read(program), f"example {platform} binary".encode())
                shared.append(manifest)
        # One plugin: the registry refuses packages whose plugin.json differs beyond `run`.
        self.assertTrue(all(m == shared[0] for m in shared))

    def test_development_folder_is_the_staged_platform(self):
        """dist/<id>/ holds one platform's files, the one asked for."""
        platforms = list(PACKAGE.PLATFORMS)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}):
            PACKAGE.package(self.root, self.binaries, platforms, stage="windows")
            PACKAGE.package(self.root, self.binaries, platforms, stage="macos")
        folder = self.root / "dist/my-plugin"
        self.assertEqual(json.loads((folder / "plugin.json").read_text())["run"], {"macos": ["bin/macos/plugin"]})
        self.assertEqual(sorted(p.name for p in (folder / "bin").iterdir()), ["macos"])

    def test_local_package_only_advertises_available_platform(self):
        """A development package does not claim binaries for other systems."""
        archives, entry = PACKAGE.package(self.root, self.binaries, ["linux"])
        self.assertEqual([a.name for a in archives], ["my-plugin-0.1.0-linux.rgplugin"])
        self.assertEqual(list(json.loads(entry.read_text())["source"]), ["linux"])
        with zipfile.ZipFile(archives[0]) as zipped:
            self.assertEqual(json.loads(zipped.read("plugin.json"))["run"], {"linux": ["bin/linux/plugin"]})
            self.assertNotIn("bin/windows/plugin.exe", zipped.namelist())

    def test_missing_platform_binary_is_rejected(self):
        """An incomplete release fails instead of silently publishing one platform."""
        (self.binaries / "windows/plugin.exe").unlink()
        with self.assertRaisesRegex(ValueError, "missing or empty windows"):
            PACKAGE.package(self.root, self.binaries, list(PACKAGE.PLATFORMS))
        self.assertFalse(list((self.root / "dist").glob("*.rgplugin")) if (self.root / "dist").exists() else [])

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
