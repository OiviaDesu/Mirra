# Keystore symbol loading

The phone endpoint loads AppleKeyStore functions through `load_keystore` in
`src/phone/endpoint.m`. Normal exported symbols always take priority. When a
required export is missing, the same loader tries a verified image-relative
address before adding the function to the existing missing-symbol report.
The protocol endpoint and device self-check use the same typed `KeyStore` API.

## Guarded fallback

The map in `src/phone/keystore-symbols.h` comes from
[@OiviaDesu](https://github.com/OiviaDesu)'s
[PR #4](https://github.com/eiyuumiru/Mirra/pull/4), commit `a193c167`.
It contains 14 MobileKeyBag functions and four CryptoTokenKit parameter functions.
The fallback requires iOS build `19H422` (15.8.8), generic arm64, the exact loaded
image paths and UUIDs, and aligned addresses in executable `__TEXT,__text`.
Malformed metadata, other builds and arm64e are rejected. The endpoint reuses
its existing platform queries and keeps loaded image handles alive.

This is part of the normal endpoint build. There is no alternate endpoint or
OS-specific build option. Existing exports, certificate handling, session
ownership and request/reply behavior remain on the existing path. A matching
address does not establish private API behavior or general iOS 15 support.

The ordinary endpoint compiler also uses the map with its reviewed import-stub
fallback, and records the map hash in the build report. The added dyld and C
imports are bounded by the existing allowlist; unknown imports remain errors.

## Checks

```sh
make symbols
```

Host fixtures cover original symbol priority, missing-symbol reporting, shared
API loading, image/CPU mismatches, command bounds, address arithmetic and owned
session cleanup. These fixtures never execute a mapped private function.

With a full iPhoneOS SDK, `make build/endpoint-check` builds the device self-check.
Sign it on the bridge with `ldid` and `config/endpoint.plist`, as for the normal
endpoint. It loads the production API, creates and unloads one unsaved keybag,
generates a signing key and serializes parameters. It never uses system keybags
or prints key material. macOS is rejected before any keybag calls.

## Device evidence

@OiviaDesu reports symbol resolution, keybag/parameter checks, certificate export,
attested-key generation and provisioning on iPhone 6s Plus (`iPhone8,2`), iOS
15.8.8 (`19H422`), Dopamine rootless (version not recorded), over SSH.
The [connection report](https://github.com/eiyuumiru/Mirra/issues/3#issuecomment-6747391254)
uses an OCLP MacBookPro10,1 on build `25G332`, with the separate RemoteScreen
repair and `no_fls` decoder route. Those results concern the contributor's
original PR build. The integrated loader still needs a device run.

[Issue #1](https://github.com/eiyuumiru/Mirra/issues/1) concerns helper execution
paths; [issue #2](https://github.com/eiyuumiru/Mirra/issues/2) concerns missing
symbols. The OCLP crash in [issue #3](https://github.com/eiyuumiru/Mirra/issues/3)
is covered in [Media transport](Media.md). These are separate failures.
