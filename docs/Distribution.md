# Distribution

## Source code

GitHub provides **Source code (zip)** and **Source code (tar.gz)** downloads for
each tag on the [Releases page](https://github.com/eiyuumiru/Mirra/releases).
Download the selected release's source, or check out its tag in the repository.
Open the project directory and follow [Building](Build.md):

```sh
make test
make app
```

Native interface, keystore and media checks require the relevant SDKs and host
frameworks. Device behavior is tested separately on the affected configuration.

GitHub archives the tracked repository contents. Review tracked files before
tagging a release and keep generated builds and private device material out of
version control. `config/sources.json` lists the inputs validated by the app
builder; it does not filter GitHub's source downloads.

## App package

```sh
make package
```

The package is named `Mirra-v<version>.dmg`, using the version in `VERSION`, and
is created under `dist/app`. It contains Mirra.app and an Applications shortcut.
The app includes compiled helpers, a Python runtime and upstream notices, but
no saved bridge credentials.

The packager checks the app signature before and after staging and verifies
the disk image with `hdiutil`. It prints the path and SHA-256. To produce an
optional ZIP after building:

```sh
python3 scripts/package.py --format zip
```

For relocation checks, mount the DMG, copy the app, eject the image and verify
the copied app's signature and bundled runtime. See [Installation](Installation.md)
for installation and signing details, and [Release guide](Release.md) for publication.
