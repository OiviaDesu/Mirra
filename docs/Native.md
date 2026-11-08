# Native integration

Mirra prepares Apple's installed iPhone Mirroring app for normal launches from
Applications, Launchpad, the Dock or Spotlight. The system app supplies the
Mirroring interface.

## Startup

With a configured bridge, run native support from the source checkout:

```sh
make support
```

The controller starts the bridge and a temporary LaunchServices adapter. The
adapter adds media/socket settings only when the bundle ID and executable path
match the system app. Other application launches retain their environment.
The socket helper verifies the caller's UID and executable path.

The default CLI session lasts 30 minutes. `--until-stopped` removes that deadline;
`--recover-backend` retries failed workers after cleanup. `--manage-usb` manages
the configured USB forwarding port. Direct SSH does not need forwarding.
The installed [background service](Service.md) manages continuous support.

Quitting Mirroring leaves support available for another launch. Stopping support
restores the temporary native registrations and closes app instances using that
session's adapters. Apple's executable and launch plists on disk are preserved.

## Lifecycle

Each worker has a separate attempt directory. State travels over a pipe and is
written atomically; rotating logs are not used as the state channel. Lifelines,
ownership journals and process birth timestamps identify abandoned workers.

Recovery waits for child processes and native registration cleanup. Observation
failures defer recovery. A restarted bridge has new endpoint sessions, so old
protocol requests are not replayed. Apple's app may need **Connect** again after
backend recovery.

See [Diagnostic storage](Service.md#diagnostic-storage) for logs and retention.

## Notifications and phone apps

Apple's notification forwarding and phone-app search are separate native
features. Mirra does not currently guarantee either feature on Hackintosh.
See Apple's [Mirroring guide](https://support.apple.com/en-us/120421) and
[notification guide](https://support.apple.com/en-us/120684).

A read-only capability report is available:

```sh
make features
python3 scripts/features.py --json
```

It reports the available notification settings and phone-app catalog. Missing
or incompatible queries return unknown status. A readable setting or catalog
does not establish notification delivery or app launching.

Remote app placeholders contain launch metadata rather than runnable Mac
executables. Test phone-app launching through Apple's Spotlight or notification
route, not by opening a placeholder bundle.

## Validate a change

Connect the target, check screen output and input, then quit and reconnect.
Test recovery after a bridge interruption. Audio, notifications, phone-app
launching, login and sleep/wake need their own checks on the affected host.
Record observed results rather than treating loaded adapters or readiness logs
as successful screen output.
