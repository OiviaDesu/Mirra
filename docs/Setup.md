# Command-line bridge setup

For guided setup, open Mirra and choose **Settings → Set up Mirra**. The steps
below provide the source checkout's CLI alternative. The bridge needs **iOS 15
or later**, an active jailbreak, OpenSSH and `ldid`; see [Bridge compatibility](Bridges.md).

## Create a profile

Inspect attached devices when using USB:

```sh
python3 scripts/doctor.py --devices --show-identifiers
```

Create a profile using the intended device's identifier and SSH account:

```sh
python3 scripts/provision.py init --output private/bridge/access.json --user SSH_ACCOUNT --serial DEVICE_IDENTIFIER
```

Alternatively, use `--product PRODUCT_IDENTIFIER` as a model filter. Direct SSH
uses `--transport ssh --host ADDRESS`. `--port` selects the direct SSH port or
the local USB forwarding port; `--device-port` selects the USB phone's SSH port.

The profile receives a new private client key. Existing destination files are
preserved. Review the generated profile's paths and device selection.

For USB, start forwarding in another Terminal:

```sh
python3 scripts/proxy.py --config private/bridge/access.json
```

An installed service may already own the forwarding port. Use that verified
forwarder or stop it before starting another listener.

## Verify the host and authorize access

Get the device's SSH host fingerprint from its terminal or an already trusted
connection. For example, run on the phone using its actual public host-key path:

```sh
ssh-keygen -lf PATH_TO_SSH_HOST_PUBLIC_KEY -E sha256
```

The phone's host key is different from the client key generated on the Mac.
Enroll the verified fingerprint:

```sh
python3 scripts/provision.py pin-host --config private/bridge/access.json --fingerprint 'SHA256:VERIFIED_HOST_FINGERPRINT'
```

The presented host key is saved only if it matches. Enrollment does not replace
an existing pin. `--host-key-type` selects a supported SSH host-key type.

Authorize the client key:

```sh
python3 scripts/provision.py authorize --config private/bridge/access.json --interactive
```

OpenSSH handles password entry in the Terminal. The program preserves existing
authorized keys and verifies login with the new key. An already trusted profile
for the same host, port and account can instead authorize access using
`--bootstrap-config PATH`.

## Build and deploy

```sh
make endpoint
python3 scripts/provision.py deploy --config private/bridge/access.json --output private/bridge/ready.json
```

See [Building](Build.md) for endpoint SDK selection. Deployment creates a unique
directory, uploads and signs the helper, and checks the required capabilities.
`--remote-root PATH` changes the normal deployment root. RootHide uses separate
code/data locations described in [Bridge compatibility](Bridges.md#dopamine-with-roothide).

The endpoint retrieves its cached MobileActivation certificate. If the cache is
missing, `--issue-certificate` requests issuance through the device's activation
service. Guided setup handles this after **Prepare connection** is selected.
An unrelated SSH, signing or helper-launch failure does not trigger issuance.

Deployment downloads the certificate, checks its digest and DER format, verifies
endpoint binding, and creates and validates an isolated attested key. A ready
profile is written after those checks succeed.

```sh
python3 scripts/doctor.py --config private/bridge/ready.json --backend --json
```

## Install support

Stop any temporary USB proxy or manual support process, then install the saved
profile:

```sh
python3 scripts/service.py prepare --config private/bridge/ready.json
python3 scripts/service.py install
python3 scripts/service.py status
```

For an existing installation, stop support and use `prepare --replace --config
PATH`. See [Background support](Service.md) for updates and cleanup.

Connect the locked target in Apple's app and verify screen output and input.
For a failure, retain the setup log and report the stage, native error or missing
capability with private details removed.
