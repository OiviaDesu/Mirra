# Third-party notices

Mirra's original source and artwork use the [MIT License](LICENSE),
copyright © 2026 eiyuumiru.

[@OiviaDesu](https://github.com/OiviaDesu) contributed the keystore symbol map,
fallback lookup and device self-check in [PR #4](https://github.com/eiyuumiru/Mirra/pull/4).
The same contributor provided the OCLP RemoteScreen service repair. Both
adaptations retain co-author credit and are part of Mirra's MIT-licensed source.

Apple's iPhone Mirroring application and system frameworks are external
components belonging to Apple. They are not included in Mirra's packages.

[MtkWireless](https://github.com/Dino-VN/MtkWireless) and the
[HighDelay NVIDIA driver](https://github.com/HighDelay/nvidia-macos-driver/releases)
are external dependencies and retain their own terms.

App packages bundle CPython from Astral's
[python-build-standalone](https://github.com/astral-sh/python-build-standalone).
`config/python.json` records the pinned archives and SHA-256 digests. Upstream
Python and dependency notices are retained in
`Contents/Resources/Support/python/licenses` and `distribution.json`, including
notices accompanying bundled pip. The source repository includes the runtime
downloader and configuration rather than the Python binaries.
