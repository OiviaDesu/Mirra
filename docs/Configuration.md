# Configuration

Guided setup creates and saves a bridge profile. For manual configuration, copy
`config/example.json` to `config/local.json` and edit the local copy. The local
file is ignored by Git.

Commands accept `--config PATH` or the `MIRRA_CONFIG` environment variable.
Relative paths in the JSON resolve against the project root. Unknown settings
and invalid values are rejected.

The **bridge** provides cryptographic operations and its matching certificate.
The **target** is the iPhone selected in Apple's Mirroring window. Bridge
selection settings do not select the target.

## USB

Set `backend.transport` to `usb` and identify the bridge using
`backend.usb.serial`, `backend.usb.product_type`, or both. Both must match when
provided. Ambiguous selection is refused. Guided setup uses the stable serial
and can select a device even when its model lookup is unavailable.

```sh
make devices
python3 scripts/doctor.py --devices --show-identifiers
make proxy
```

The identifier report is for local configuration. `listen_port` is the host's
loopback forwarding port; `device_port` is the phone's SSH port. The selected
USB device is checked again for each new connection.

Native CLI support can manage forwarding with `--manage-usb`. Stop a separate
proxy first, since two listeners cannot own the same port.

## Wi-Fi or direct SSH

Set `backend.transport` to `ssh`, then set `backend.ssh.host` and `port`.
The bridge's address must remain reachable. If DHCP changes its address, update
the profile; a stable hostname or reservation can help.

The SSH account, private key and host pin are stored in separate files referenced
by the profile. Do not put passwords or key contents in JSON. Host-key verification
is required. Reuse a `host_key_alias` across transports only after verifying that
both connections reach the same device.

`remote_endpoint` and `remote_certificate` select absolute paths on the bridge.
`certificate_file` points to the matching local certificate. Paths containing
spaces are supported.

## RootHide paths

Guided setup detects RootHide's `jbroot` and `rootfs` tools and saves
`backend.ssh.path_mode` as `roothide`. Remote paths use the bootstrap's virtual
namespace. The bootstrap shell resolves executable paths, and certificate paths
are converted for each invocation.

The default mode is `native`. Unknown modes or unavailable converters cause a
configuration or launch error. See [Dopamine with RootHide](Bridges.md#dopamine-with-roothide)
for code locations and account permissions.

## Decoder route

`host.decoder_route` accepts `auto`, `normal` or `no_fls`. Use the default `auto`
unless diagnosing a configuration covered by [Decoder compatibility](Decoder.md).

## Apply a profile

The installed service keeps a private configuration snapshot. To change its
bridge or transport, stop support and use **Import…** in Settings, or run:

```sh
python3 scripts/service.py stop
python3 scripts/service.py prepare --replace --config PATH
python3 scripts/service.py install
```

Omitting `--config` during replacement preserves the installed bridge settings.

## Diagnostics

```sh
make doctor
python3 scripts/doctor.py --backend --json
```

The host report reads configured prerequisites. `--backend` also starts the
provisioned endpoint, creates and validates a disposable attested key, then
closes it. USB diagnostics need a running forwarder.

The endpoint greeting includes a certificate digest, which must match the local
certificate. Legacy greetings without a digest cannot report verified binding.
Target authentication and media are checked in a connected Mirroring session.
