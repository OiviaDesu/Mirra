# Decoder compatibility

Mirra can apply a compatibility hook for H.264 decoder creation failures on the
FLS software path with Display P3 streams. Automatic selection uses the incoming
stream's color metadata and decoder options, not the Mac model or display settings.

## Automatic selection

On the inspected Intel build `25G332`, native support loads the compatibility
hook. It removes `NegotiationDetails` only for H.264 streams whose options contain
`FLS` and whose SPS identifies Display P3 (color primaries 12). Other streams and
options pass through unchanged. The selected policy and actual decoder results
are recorded in the logs. Uninspected architectures and builds use the normal route.
This includes macOS 15: accepting a host's media interfaces does not automatically
enable a decoder hook that has not been inspected on that OS.

If automatic setup cannot unload AVConference, Mirra restores and verifies
Apple's original service before continuing with the normal decoder. The logs
record the launchctl exit status, rejection text, and fallback. This permits
the rest of native support to start; it does not establish successful video
decoding. A Display P3/FLS problem can still require the compatibility hook.
Unverified restoration still stops startup, and an explicit `no_fls` request
does not fall back silently.

The advanced `host.decoder_route` setting accepts:

| Value | Behavior |
| --- | --- |
| `auto` | Handle Display P3/FLS H.264 streams on the supported Intel build |
| `normal` | Use the normal decoder route |
| `no_fls` | Remove `NegotiationDetails` for all H.264 streams on the supported Intel build |

Apply profile changes through [Configuration](Configuration.md). The setting
controls decoder compatibility rather than image sharpening or output resolution.

## Hook behavior

The temporary `avconferenced` process loads `decoder-route.dylib`. At
`VCPDecompressionSessionCreate`, the hook copies the H.264 decoder specification,
removes `NegotiationDetails` when the selected policy matches, and calls the
original function. Compressed samples, format, pixel-buffer attributes, callbacks
and color metadata are preserved.
The hook does not force a hardware preference.

The FLS path has produced decoder-creation status `-8969` for Display P3 streams
on both laptop and desktop profiles, including after the outer session was created.
Inspect the actual decoder result and hardware-property query when diagnosing
a host; a loaded hook alone does not establish successful decoding.

## Lifetime and restoration

The route remains active while background support runs, including after the
Mirra window closes. **Stop** ends it. A guardian restores Apple's AVConference
registration after a normal stop or verified supervisor failure.

An idle AVConference job remains registered for the next connection. When macOS
starts a new decoder process, support waits for its hook marker without restarting
the bridge. Unavailable observations defer readiness and are checked again.

Recovery verifies the owned configuration and defers on observation errors.
Pending recovery records prevent replacing an installed package before cleanup
finishes. Apple's application and launch plist on disk are not edited.

## Logs

Open Settings and choose **Show log**. Decoder messages include the route,
hook-loaded process, stream dimensions/color information, creation status and
available hardware-property results. An unavailable query remains unknown.

Dated native logs use the app's seven-day retention policy. Compressed video
is not recorded. See [App logs](App.md#logs) and [Media](Media.md).
