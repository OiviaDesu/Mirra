# Background support

The per-user LaunchAgent keeps bridge and native launch support available
without an open Terminal. Guided setup installs it; source users can manage it
with the CLI below after building and provisioning a bridge.

## Install and control

Stop competing support processes and USB proxies before installation.

```sh
python3 scripts/service.py prepare --config PATH
python3 scripts/service.py install
python3 scripts/service.py status
```

`prepare` creates a private package and launch definition. `install` registers
and starts the job. The package contains a configuration snapshot; editing the
source profile does not change a running service.

Status reports registration, native launch support and bridge health. Readiness
requires matching live process identities. It does not establish target video.

```sh
python3 scripts/service.py stop
python3 scripts/service.py start
python3 scripts/service.py remove
```

`stop` unloads support and waits for cleanup while retaining login registration.
`start` loads it again. `remove` stops support and removes the owned registration,
preserving private setup, logs and the installed package.

## macOS data-access prompts

The native socket helper uses a temporary directory inside iPhone Mirroring's
container. macOS may ask whether `python3` can access data from other apps; the
request belongs to Mirra's bundled background interpreter. The permission can
expire when that interpreter exits. It is not an iPhone jailbreak or T2 check.

If startup fails, support now waits without retrying in the same interpreter.
The window shows the failure and a recovery action instead of staying on
"Preparing app support". Go back to Settings, stop support, then start it to
retry deliberately. Allow the macOS request if you want Mirra to use that
temporary directory. If the folder is missing, open and quit Apple's iPhone
Mirroring once before retrying. A later startup error still needs its own
diagnosis in the service log; granting file access does not prove readiness.

An interrupted startup is also held on the next launchd invocation. A normal
stop rearms it. Bridge reconnection inside a running native session remains
automatic. No Full Disk Access grant or privacy-database changes are required
by this fix. See Apple's [macOS app-data privacy explanation](https://developer.apple.com/videos/play/wwdc2023/10053/).

## Update or switch profiles

```sh
python3 scripts/service.py stop
python3 scripts/service.py prepare --replace
python3 scripts/service.py install
```

Without `--config`, replacement preserves the installed profile and credentials.
Add `--config PATH` to select another bridge. Cleanup must finish before replacement.
The previous package is archived for rollback.

Transport changes update the owned launch definition atomically. Conflicting
or unfamiliar jobs are not overwritten. Previous packages are retained until
removed explicitly.

## Installed files

| Path under the user's home directory | Purpose |
| --- | --- |
| `Library/Application Support/Mirra/current` | Service code, adapters and Python runtime |
| `Library/Application Support/Mirra/current/private` | SSH access and device certificate |
| `Library/Application Support/Mirra/current/config.json` | Installed profile |
| `Library/Application Support/Mirra/current/runtime` | Session and attempt state |
| `Library/Application Support/Mirra/logs` | Application and service diagnostics |
| `Library/Application Support/Mirra/service.json` | Current or last session pointer |
| `Library/Caches/Mirra` | Ownership and recovery records |
| `Library/LaunchAgents/local.mirra.agent.plist` | Login registration |

Package hashes and host compatibility are verified before startup. Apple's app
is used from `/System/Applications/iPhone Mirroring.app` and is not copied into
the package. Private profiles and installed service folders are not distributable
app packages.

## Diagnostic storage

Service diagnostics rotate at 2 MiB with one tail backup. Bursts can briefly
exceed the threshold between rotation checks. SSH stderr keeps a bounded memory
tail. App log files follow the [dated retention policy](App.md#logs).

After verified cleanup, support retains three completed backend attempts and
three completed native sessions, plus the active attempt and session. Live,
unfinished or unfamiliar directories are preserved. Installation backups are
not automatically pruned.

Temporary broker workspaces use a lease. They are removed after sharingd is
restored and the lease is unused. Cleanup verifies ownership and does not follow
substituted symlinks. Retention events appear in the service log; a deferred
result means the data was preserved because verification did not finish.

## Recovery

Launchd restarts a failed supervisor. Backend workers retry independently after
their children exit and the previous native registrations are restored. The
controller verifies process birth times, staged configuration and exclusive
ownership before recovering an abandoned job.

A failed observation does not authorize a replacement. Unresolved cleanup keeps
its recovery record and blocks package replacement until restoration finishes.
Updating stopped support first runs the existing recovery routines against its
installed runtime. The updater holds the backend session lock and checks children
recorded by pending sharing recovery before moving that runtime. It preserves
the package if a controller or guardian is still alive, the GUI domain cannot be
queried, or restoration cannot be verified. After recovery succeeds, it archives
the old package and installs the update using the saved bridge configuration.
Target Mirroring may still need a new Connect action after backend recovery.

## Python runtime

The service uses its own verified Python copy. Its launch command does not
depend on the app's current location, so moving or closing the app leaves
background support available. A missing or modified recorded runtime file
prevents startup.
