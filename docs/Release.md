# Release guide

`VERSION` supplies the release version. `config/release.json` supplies internal
bundle metadata and the repository URL. Package filenames use that version.

## Prepare

- Update the changelog and compatibility documentation for the change.
- Run relevant source and native checks, plus device tests for runtime changes.
- Build from a clean checkout or the source for the selected release tag.
- Check the app signature, bundled runtime and third-party notices.
- Mount the DMG, verify the Applications shortcut and test app relocation.
- Review screenshots and exported files for private data.

## Build the DMG

```sh
make package
```

The command creates `Mirra-v<version>.dmg` under `dist/app` and prints its SHA-256.
Create a checksum file for the DMG selected for the release.

## Publish

Review the staged source, Git identity and repository remote before committing.
Keep generated builds, private profiles and runtime logs out of version control.
Tag the selected version and attach the app DMG and checksum file to the release.
GitHub automatically provides **Source code (zip)** and **Source code (tar.gz)**
downloads for that tag.

Build and packaging commands create local files; publication is a separate step.
Release notes should identify Mirra as experimental and describe the change,
tested configurations, compatibility limits and signing status. Use the DMG to
install the app and GitHub's source downloads to build it.

See [Publishing](Publishing.md), [Distribution](Distribution.md) and
[Screenshots](Screenshots.md).
