# Building

## Requirements

Use Python 3.9 or later, Apple's Command Line Tools and a macOS SDK containing
the required private framework stubs. The Python tools use the standard library.
The host and framework restrictions are listed in
[Compatibility](../README.md#compatibility).

## Build the app

```sh
make app
```

This builds the native interface, adapters, phone endpoint and SSH password
helper. The app is available at `build/Mirra.app`. Completed builds are retained
under `build/versions.noindex`; the app shortcut points to the latest build.

The builder downloads a pinned CPython runtime and its upstream notices.
`config/python.json` records archive URLs and checksums. Verified downloads are
cached. The bundled runtime is read-only, and helpers run without writing bytecode
into the signed app. Installed background support receives its own runtime copy.

## Native components

```sh
make build
```

| Output | Purpose |
| --- | --- |
| `build/keystore.dylib` | Bridge cryptographic operations through sharingd |
| `build/media.dylib` | AWDL media compatibility |
| `build/sockets.dylib` | Delegated socket binds |
| `build/launch.dylib` | Adapter settings during LaunchServices startup |
| `build/decoder-route.dylib` | Optional decoder compatibility hook |
| `build/inspector` | Host and private-interface compatibility report |

`make app` builds all required adapters. See [Architecture](Architecture.md)
for their runtime roles and [Native integration](Native.md) for CLI startup.

## Phone endpoint

```sh
make endpoint
```

The build prefers a full iPhoneOS SDK. A limited `macosx-headers` fallback uses
macOS headers and project-authored link stubs for the required iOS imports.
Select the method explicitly when needed:

```sh
python3 scripts/endpoint.py --sdk iphoneos
python3 scripts/endpoint.py --sdk macosx-headers
```

The output is `build/endpoint`, with an adjacent build report. The deployment
target is iOS 15 with generic arm64. Build and deployment verify the binary's
platform and CPU subtype; API compatibility also needs validation on the bridge.
The fallback is not a general replacement for an iPhoneOS SDK.

The endpoint is signed on the phone during [Bridge setup](Setup.md) using `ldid`
and `config/endpoint.plist`.

## Checks

Run the checks appropriate to the change:

| Command | Coverage |
| --- | --- |
| `make test` | Python configuration, transport, lifecycle and export tests |
| `make interface` | Native navigation, controls, dialogs, appearance and logs |
| `make authorization` | SSH password helper and secure input channel |
| `make keystore` | Bridge protocol and owned-session cleanup |
| `make media` | Media hook behavior and incompatible-interface rejection |
| `make decoder` | Decoder option filtering, stream metadata and argument preservation |
| `make certificate` | Certificate API handling using isolated fixtures |

Native UI tests run as a temporary app through LaunchServices. Device-facing
effects are replaced with fixtures. Test bundles are kept outside Spotlight
indexing and removed afterward.

Changes affecting device setup, authentication or media also need a connected
session on the affected configuration. See [Testing priorities](Roadmap.md).

## Packages

```sh
make package
```

`make package` creates `Mirra-v<version>.dmg` under `dist/app`, containing Mirra.app
and an Applications shortcut. The filename uses `VERSION`; internal bundle
metadata comes from `config/release.json`. The command prints the path and SHA-256.

To build a tagged release, use GitHub's **Source code (zip)** or
**Source code (tar.gz)** download and open the extracted project directory.

For an optional app ZIP after building:

```sh
python3 scripts/package.py --format zip
```

See [Distribution](Distribution.md) for archive contents and verification,
[Installation](Installation.md) for signing prompts, and [NOTICE.md](../NOTICE.md)
for bundled component notices.
