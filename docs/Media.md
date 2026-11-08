# Media transport

The `awdl-single-tile` profile adapts Apple's media transport for the supported
host. Bridge selection is configured separately. Native authentication,
encryption, lock-state checks and response callbacks are preserved.

## Profile behavior

| Area | Behavior |
| --- | --- |
| UDP parameters | Include peer-to-peer traffic, permit local address reuse and require AWDL |
| IPv6 endpoints | Fill missing link-local scope and use the local AWDL address while preserving the source port |
| Listener handoff | Release the matching UDP reservation before media reuses its port |
| Stream start | Translate media type 3 to socket type 1, remote traffic flags to `0x10000` and stream flags to Handoff (`4`) |
| Local traffic flags | Translate exactly `0x10000` to zero |
| Local stream flags | Translate exactly `6` to zero for ordinary AWDL |
| Video tiles | Offer one tile when the native offer contains more than one |
| Other fields | Preserve native bitrate, codec, dimensions, FEC and unrelated values |

The adapter returns the actual Network.framework result. Video uses Apple's
renderer and the host's VideoToolbox decoder. See [Decoder compatibility](Decoder.md)
for the optional decoder route.

Wi-Fi conditions and decoder behavior affect motion and display quality.
Inspect packet loss, stream dimensions and decoder errors before changing
transport settings. Router channels and driver installation are external to Mirra.

## Compatibility check

```sh
make build
build/inspector
```

The inspector reports architecture, macOS build, framework versions, private
symbols and Objective-C method signatures. Package preparation and native startup
require a passing result. Unsupported builds are rejected because matching
Objective-C signatures alone cannot establish the private C-function ABI.

The adapter repeats its interface checks before installing hooks. See
[Compatibility](../README.md#compatibility) for the accepted host and app versions.

Supporting another build requires reviewing function signatures and ownership,
checking socket binds and listener cancellation, and testing a connected media
session. Changing the accepted build string alone is insufficient.

## Validation

```sh
make test
make media
```

The native media checks exercise translation rules, metadata and callback
preservation, tile counts and flag combinations with isolated recording functions.
They also verify that incompatible method signatures leave hooks unapplied.

Validate video, audio, input and reconnection on the affected devices after a
transport or decoder change.
