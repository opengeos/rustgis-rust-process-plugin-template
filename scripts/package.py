#!/usr/bin/env python3
"""Build a native plugin, or pack CI binaries into one reproducible .rgplugin per platform."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = {"linux": "plugin", "macos": "plugin", "windows": "plugin.exe"}
ZIP_TIME = (2024, 1, 1, 0, 0, 0)


def platform_name() -> str:
    """Return the RustGIS platform key for this computer.

    Raises:
        ValueError: If this operating system is unsupported.
    """
    names = {"linux": "linux", "darwin": "macos", "win32": "windows"}
    if sys.platform not in names:
        raise ValueError(f"unsupported operating system: {sys.platform}")
    return names[sys.platform]


def archive(files: dict[str, bytes]) -> bytes:
    """Zip a package's files reproducibly, with Unix modes.

    Args:
        files: The package's files by name.

    Returns:
        The .rgplugin's bytes.

    Raises:
        ValueError: If the package is larger than the registry takes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name.startswith("bin/") else 0o100644) << 16
            zipped.writestr(info, data)
    data = buffer.getvalue()
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("package exceeds the registry's 64 MiB limit")
    return data


def package(root: Path, binaries: Path, platforms: list[str], tag: str | None = None, stage: str | None = None) -> tuple[list[Path], Path]:
    """Validate metadata and pack each supplied executable into its platform's package.

    Each package holds the same plugin.json, but for `run`, which names that platform's
    program: RustGIS downloads the one for the platform it runs on.

    Args:
        root: Project folder containing plugin.json, Cargo.toml and LICENSE.
        binaries: Folder with one subfolder per platform and its executable.
        platforms: Platform keys to package, normally all three for a release.
        tag: Optional release tag that must match the manifest version.
        stage: The platform whose files go to dist/<id>/ for development; the first of
            `platforms` when it isn't one of them.

    Returns:
        Paths to the packages, in the order of `platforms`, and to the registry entry.

    Raises:
        ValueError: If the metadata or a required executable is invalid.
    """
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
    plugin_id, version = manifest.get("id", ""), manifest.get("version", "")
    if not isinstance(plugin_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", plugin_id):
        raise ValueError("plugin id must be 1-64 lowercase letters, digits or hyphens, starting with a letter")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("version must look like 1.0.0")
    if version != cargo["package"]["version"]:
        raise ValueError("plugin.json and Cargo.toml versions must match")
    if tag and tag != f"v{version}":
        raise ValueError(f"release tag must be v{version}")
    if manifest.get("runtime") != "process" or "entry" in manifest or "python" in manifest:
        raise ValueError("a native template needs runtime process, without entry or python")
    expected = {key: [f"bin/{key}/{name}"] for key, name in PLATFORMS.items()}
    if manifest.get("run") != expected:
        raise ValueError("run must name bin/<platform>/plugin (plugin.exe on Windows)")
    if not platforms:
        raise ValueError("no platform to package")
    # Every executable is checked before anything is written: a release is all its platforms.
    for key in platforms:
        binary = binaries / key / PLATFORMS[key]
        if not binary.is_file() or not binary.stat().st_size:
            raise ValueError(f"missing or empty {key} executable: {binary}")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/?", manifest.get("homepage", ""))
        if not match:
            raise ValueError("set plugin.json homepage to your GitHub repository")
        repo = match[1]
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    stage = stage if stage in platforms else platforms[0]
    package_paths, sources = [], {}
    for key in platforms:
        files = {
            "plugin.json": (json.dumps(manifest | {"run": {key: expected[key]}}, indent=2) + "\n").encode(),
            "LICENSE": (root / "LICENSE").read_bytes(),
            expected[key][0]: (binaries / key / PLATFORMS[key]).read_bytes(),
        }
        data = archive(files)
        package_path = dist / f"{plugin_id}-{version}-{key}.rgplugin"
        package_path.write_bytes(data)
        package_paths.append(package_path)
        sources[key] = {
            "url": f"https://github.com/{repo}/releases/download/v{version}/{package_path.name}",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if key == stage:
            folder = dist / plugin_id
            # Only generated files are staged here, replacing an earlier platform's. Other
            # local files are never packaged.
            if folder.exists():
                shutil.rmtree(folder)
            for name, content in files.items():
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if name.startswith("bin/"):
                    target.chmod(0o755)
    # A package per platform: the registry lists each, and RustGIS downloads its own.
    entry = {"id": plugin_id, "source": sources, "categories": ["Utilities"]}
    entry_path = dist / f"{plugin_id}.json"
    entry_path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    return package_paths, entry_path


def main() -> int:
    """Build and package for local use, or package three CI artifacts for a release.

    Returns:
        Zero on success, one when validation or compilation fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binaries", type=Path, help="package the linux/, macos/ and windows/ artifact folders without building")
    parser.add_argument("--tag", help="release tag, for example v0.1.0")
    args = parser.parse_args()
    try:
        if args.binaries:
            binaries = args.binaries
            platforms = list(PLATFORMS)
        else:
            subprocess.run(["cargo", "build", "--release", "--locked"], cwd=ROOT, check=True)
            platform = platform_name()
            platforms = [platform]
            binaries = ROOT / "dist" / ".binaries"
            target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
            if not target.is_absolute():
                target = ROOT / target
            destination = binaries / platform / PLATFORMS[platform]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target / "release" / PLATFORMS[platform], destination)
        if args.tag and not args.binaries:
            raise ValueError("release packaging requires --binaries with all three platforms")
        # The development folder is this computer's platform, so it can be run here.
        here = platform_name() if sys.platform in ("linux", "darwin", "win32") else None
        package_paths, entry_path = package(ROOT, binaries, platforms, args.tag, here)
        for package_path in package_paths:
            print(package_path)
        print(entry_path)
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
