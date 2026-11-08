# Decoder compatibility

Mirra can apply a compatibility hook for H.264 decoder creation failures on the
FLS software path with Display P3 streams. Selection depends on the host profile,
not the display's configured color space.

## Automatic selection

Native support reads the architecture, macOS build and `hw.model`. On the
supported Intel build `25G332`, the `MacPro7,1` profile selects `no_fls`.
Other models use the normal decoder. The selected route is recorded in the logs.

The advanced `host.decoder_route` setting accepts:

| Value | Behavior |
| --- | --- |
| `auto` | Select from the supported host profiles |
| `normal` | Use the normal decoder route |
| `no_fls` | Apply the hook on the supported Intel build |

Apply profile changes through [Configuration](Configuration.md). The setting
controls decoder compatibility rather than image sharpening or output resolution.

## Hook behavior

The temporary `avconferenced` process loads `decoder-route.dylib`. At
`VCPDecompressionSessionCreate`, the hook copies the H.264 decoder specification,
removes `NegotiationDetails` and calls the original function. Compressed samples,
format, pixel-buffer attributes, callbacks and color metadata are preserved.
The hook does not force a hardware preference.

The FLS path has produced decoder-creation status `-8969` for Display P3 streams.
Inspect the actual decoder result and hardware-property query when diagnosing
a host; a loaded hook alone does not establish successful decoding.

## Lifetime and restoration

The route remains active while background support runs, including after the
Mirra window closes. **Stop** ends it. A guardian restores Apple's AVConference
registration after a normal stop or verified supervisor failure.

Recovery verifies the owned configuration and defers on observation errors.
Pending recovery records prevent replacing an installed package before cleanup
finishes. Apple's application and launch plist on disk are not edited.

## Logs

Open Settings and choose **Show log**. Decoder messages include the route,
hook-loaded process, stream dimensions/color information, creation status and
available hardware-property results. An unavailable query remains unknown.

Dated native logs use the app's seven-day retention policy. Compressed video
is not recorded. See [App logs](App.md#logs) and [Media](Media.md).
