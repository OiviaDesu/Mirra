# Bridge compatibility

The bridge and target are separate devices. The bridge needs a jailbreak to
run Mirra's helper; the target follows
[Apple's iPhone Mirroring requirements](https://support.apple.com/en-us/120421).

## Requirements

- An active jailbreak, OpenSSH and permission to run the signed helper.
- `ldid` available on the SSH shell's PATH or a supported jailbreak path.
- An arm64 device running iOS 15 or later, matching the endpoint's build target.
- Available AppleKeyStore and activation APIs, a matching device certificate,
  and working attested-key operations.

The reference bridge is iPhone XS on iOS 17.2. The
[keystore symbol fallback](KeyStore.md) records @OiviaDesu's iPhone 6s Plus
results and the limits of that evidence. Other devices and jailbreaks need
setup and Mirroring validation. Symbol availability and a matching architecture
alone do not establish compatibility.

Jailbreaks may impose passcode or Secure Enclave restrictions. Review the
jailbreak's documentation before testing; for example,
[palera1n's requirements](https://github.com/palera1n/palera1n#readme).

## USB and SSH

USB selection uses the bridge's stable identifier and supports a configurable
phone SSH port. Imported profiles can also filter by product identifier.
Wi-Fi SSH uses the verified bridge address and account. See
[Configuration](Configuration.md) for transport settings.

Each bridge must use its own certificate and attested keys. Mirra verifies the
key's wire format before publishing a ready profile. Unrecognized layouts need
review rather than field substitutions.

## Rootless layouts

Mirra searches for `ldid` on PATH and at standard rootful/rootless locations,
including `/var/jb/usr/bin/ldid` and `/usr/bin/ldid`. Rootless paths follow the
convention described in [Theos documentation](https://theos.dev/docs/rootless).

When the standard `/var/jb/usr/bin` bootstrap is present, default deployments
use `/var/jb/var/mobile/MirraCache/<deployment-id>` for the helper and certificate.
Each attempt creates a new directory. Existing deployments and custom roots
are preserved; RootHide converters retain priority and their separate code/data
layout. No existing cache directory is replaced or redirected.

This addresses the executable placement implicated by
[issue #1](https://github.com/eiyuumiru/Mirra/issues/1). Follow-up iOS 16 bridge
logs show the helper executing and certificate checks passing at this location.
The iOS 15 symbol-resolution problem in issue #2 is a separate compatibility
step. SIGKILL alone does not identify the original kernel rejection mechanism.

Wi-Fi host-key failures distinguish an unreachable route, a refused SSH port,
and a missing response. USB failures retain USB-specific guidance. The app
includes a local-network purpose string. These diagnostics do not change SSH
trust or bypass host-key verification.

## Dopamine with RootHide

RootHide uses a virtual bootstrap filesystem and separate locations for code
and data. Mirra verifies its path converters and installs helper code under
`/usr/local/libexec/Mirra`, with certificate data under
`/var/mobile/Library/Caches/Mirra`. Each deployment has its own directory.

The code folder may require the phone's root SSH account. If setup reports
`roothide_code_directory_unavailable`, check the account's permissions and retry
setup with the appropriate account. Mirra does not elevate privileges automatically.

Saved profiles use `backend.ssh.path_mode: "roothide"`. Executables are launched
through the bootstrap shell, and certificate paths are converted at each launch.
The randomized bootstrap root is not saved in the profile.

See RootHide's documentation for
[code locations and entitlements](https://github.com/roothide/Developer/blob/main/entitlements.md),
[bootstrap paths](https://github.com/roothide/Developer/blob/main/roothide.md) and
[path conversion](https://github.com/roothide/Developer/blob/main/interface.md).

## Test a bridge

1. Install the app on a [compatible host](../README.md#compatibility).
2. In Settings, open setup, select the bridge and verify its SSH fingerprint.
3. Choose **Prepare connection** and check helper signing, capabilities,
   certificate preparation and attested-key validation.
4. Finish setup and connect the locked target through Apple's app.
5. Check authentication, screen output, input, audio and reconnection.

Record the device, OS, jailbreak, transport and Mirra version with the results.
If a helper fails before certificate preparation, inspect its launch/signing
error rather than requesting another certificate.

For an additional bridge diagnostic with an app installed in Applications:

```sh
"/Applications/Mirra.app/Contents/Resources/Support/python/bin/python3" -B \
  "/Applications/Mirra.app/Contents/Resources/Support/scripts/doctor.py" \
  --config "$HOME/Library/Application Support/Mirra/current/config.json" \
  --backend --json
```

This starts the endpoint and checks a disposable key and certificate binding.
Keep the setup directory private and share only relevant redacted errors.
