# RustGIS Rust Process Plugin Template

A starting point for a native [RustGIS](https://github.com/opengeos/RustGIS) plugin written in Rust. The plugin is a single executable with a process manifest. Users need no Rust toolchain or Python installation to run the packaged plugin.

Process plugins run on the desktop and in `rustgis-cli`. They are **not sandboxed** and do not run in the web app. For a sandboxed Rust plugin that also runs in the browser, use [rustgis-plugin-template](https://github.com/opengeos/rustgis-plugin-template). For a Python process plugin, use [rustgis-python-plugin-template](https://github.com/opengeos/rustgis-python-plugin-template).

## Start a plugin

Click **Use this template** on GitHub, then clone your repository. Development needs Rust and Python 3.11 or later for the packaging scripts only.

1. Edit `plugin.json`: set `id`, `name`, `description`, `author` and `homepage` to your plugin and repository. Keep `runtime: "process"` and the three `run` paths. The executable is always named `plugin` (`plugin.exe` on Windows), independently of your plugin id.
2. Add your commands and tools to the manifest and dispatch them in `src/main.rs`.
3. Keep the versions in `plugin.json` and `Cargo.toml` in sync.
4. Build a package for your current computer:

   ```sh
   python scripts/package.py
   ```

The script builds with `cargo build --release --locked` and writes:

| File | Purpose |
|---|---|
| `dist/<id>/` | Development folder containing the manifest and executable |
| `dist/<id>-<version>.rgplugin` | Installable ZIP package with Unix executable permissions preserved |
| `dist/<id>.json` | Registry submission entry with the release URL and SHA-256 |

A local package advertises only the current platform. The release workflow combines all three platforms. The registry entry points to your GitHub release, which must exist before you submit it.

## Try the examples

The template has two examples:

- **Count Layers**, a command on the Plugins tab, calls `host.layers`, then `host.notify`, and returns the number of map layers.
- **Count Features**, a geoprocessing tool, reads the features supplied in its `input` layer parameter, emits progress and a log message, and returns a run message. RustGIS supplies only selected features when the input has a selection. Tools do not make host calls.

Install the development folder through **Plugins > Plugin Manager > Settings > Add Plugin Folder**, or run it on the command line:

```sh
rustgis-cli run --no-plugins --plugin dist/my-plugin --sample \
  --cmd 'plugin:my-plugin/layer-count'
rustgis-cli run --no-plugins --plugin dist/my-plugin --sample \
  --cmd 'gp.run={"tool":"plugin:my-plugin/feature-count","params":{"input":"Countries"}}'
```

Replace `my-plugin` with your manifest id. To exercise all declared commands and tools against the World Atlas sample:

```sh
python scripts/smoke.py --cli /path/to/rustgis-cli --strict
```

For an installed archive, use `rustgis-cli plugins install dist/my-plugin-0.1.0.rgplugin`. Installation does not sandbox a native plugin. Add native libraries deliberately, and arrange their distribution or installation for every supported platform.

## The process protocol

RustGIS starts a **fresh process for each command or tool invocation**, in the plugin folder. In-memory state does not persist across calls. A plugin that needs persistence must explicitly store it, for example in its own files.

`src/protocol.rs` implements the existing JSON-line protocol:

1. Read the initial request from stdin, including ABI version, kind, id, parameters, and supplied tool layers.
2. For a command, send `{"call":{"op":"host.layers","params":{}}}` and read a reply of `{"ok":...}` or `{"error":"..."}`.
3. Optionally send `{"log":"..."}` or `{"progress":"..."}` messages.
4. Send one final `{"ok":...}` or `{"error":"..."}` and exit.

Every message is flushed immediately. Keep stdout for protocol messages; send native-library diagnostics to stderr. Reads are bounded and malformed requests produce error replies. The example checks ABI version 1 and handles unknown commands, missing parameters and host errors without panicking.

No new SDK or plugin interface is required. Copy the transport module when you need another program, or adapt it behind your own application interface. The [plugin documentation](https://github.com/opengeos/RustGIS/blob/main/docs/plugins.md#process-plugins) describes permissions, files, tool outputs, tabs, panels and translations. Add plugin translations through the manifest's `i18n` table and use the request's `lang` when constructing translated messages.

## Test and check

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
python -m unittest discover -s tests -p 'test_*.py'
pre-commit run --all-files
```

Rust tests exercise protocol envelopes and run the actual executable with valid and malformed requests. Packaging tests check repeatable bytes, executable permissions, missing platform binaries and version mismatches. CI builds and tests on Linux, macOS and Windows, then packages the binaries and runs the command and tool in RustGIS on Linux. Set the `RUSTGIS_VERSION` GitHub repository variable to test a particular RustGIS release; otherwise CI uses the latest release.

## Release

1. Update both versions and commit on a feature branch through a pull request.
2. Once the change is merged, push a matching version tag, for example `v0.1.0`.
3. The Release workflow calls the same three-platform CI workflow. It builds a universal macOS executable for Apple Silicon and Intel, plus Linux x86-64 and Windows x86-64 executables.
4. After all builds, tests and the real RustGIS smoke check pass, it publishes one `.rgplugin` and its registry JSON entry in the GitHub release.

The archive contains `plugin.json`, `LICENSE` and the three `bin/<platform>/` executables. Linux is built on Ubuntu 22.04; macOS targets version 11 or later. Host permissions control RustGIS host requests but do not restrict a native program's filesystem or network access. Process commands have a host timeout; tools run until completion or cancellation. Cancellation stops the process and its children.

To test release packaging manually after downloading the three CI executable artifacts into `artifacts/linux`, `artifacts/macos` and `artifacts/windows`:

```sh
python scripts/package.py --binaries artifacts --tag v0.1.0
```

The script refuses mismatched tags or incomplete platform bundles and writes deterministic archives. To list a published version in the [plugin registry](https://plugins.rustgis.app), submit its `dist/<id>.json` as `registry/<id>.json` to [opengeos/rustgis-plugins](https://github.com/opengeos/rustgis-plugins), choosing the appropriate categories.
