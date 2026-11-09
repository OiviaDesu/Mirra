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

## Resolution and text clarity

The incoming video resolution and the pixels used to display the Mirroring
window are separate measurements. A full-resolution stream can still look soft
when the entire phone screen is reduced to a small window. Window dimensions
are measured in points; the display's backing scale determines their pixel size.
Check the video area itself, excluding the window frame.

Apple's **View > Larger** option can improve readability, but the window presets
do not guarantee one display pixel for each source pixel. A display with more
physical pixels and an appropriate HiDPI mode can retain more detail. Zooming
content or increasing its text size can also help, at the cost of showing less
content at once. Software scaling cannot reproduce every source pixel in a
smaller physical pixel area.

Decoder logs may include padded codec dimensions that differ slightly from the
visible image dimensions. This is distinct from reducing the video resolution.
Compare the decoder format, displayed video area, and backing scale before
attributing soft text to a low-resolution stream.

The [Display P3 decoder workaround](Decoder.md) preserves the stream dimensions
and color metadata. It does not sharpen the image or change window scaling.
Compression can also affect detail; dimensions alone do not establish lossless
image quality.

## Compatibility check

```sh
make build
build/inspector
```

The inspector reports architecture, macOS build, framework versions, private
symbols and Objective-C method signatures. It accepts Intel macOS 15 or later
only when the required media and launch methods and keystore exports are present
and compatible. Package preparation and native startup require a passing result.

The report distinguishes the tested reference from experimental hosts. Matching
symbols and method signatures do not prove private C-function behavior or a
working connection. New configurations still require live validation.

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
