# Changelog

## 0.1.1

- Recover pending app-launch, decoder and sharing sessions before updating
  stopped support, preserving ownership checks and the saved bridge setup.
- Fix control-error logging so an unsuccessful action returns its error reply
  without a second Python exception.
- Pause failed startup attempts instead of repeating permission prompts, and
  verify cleanup before trying to restore launch support.
- Place default rootless bridge helpers inside the detected jailbreak bootstrap,
  preserving existing deployments, custom roots and RootHide handling.
- Distinguish unreachable SSH routes, refused ports and missing responses while
  preserving host-key verification.
- Extend the existing keystore loader with the guarded symbol-map fallback and
  shared device self-check, contributed by [@OiviaDesu](https://github.com/OiviaDesu)
  in [PR #4](https://github.com/eiyuumiru/Mirra/pull/4). See [Keystore loading](docs/KeyStore.md).
- Repair the known OCLP RemoteScreen constant stub with guarded method patching,
  based on [@OiviaDesu](https://github.com/OiviaDesu)'s
  [PR #4](https://github.com/eiyuumiru/Mirra/pull/4) repair.
- Target Intel macOS 15 or later for the app and every bundled macOS helper.
  Verify binary deployment targets during app building and packaging.
- Allow experimental Sequoia setup with Apple's Mirroring 1.x app when the
  required private interfaces match. Keep the tested Tahoe decoder policy.
- Apply automatic decoder compatibility to Display P3/FLS H.264 streams on the
  supported Intel build, regardless of Mac model. This addresses connection
  failures where video arrives but decoder creation repeatedly returns `-8969`.
- Preserve bridge support while AVConference is idle or restarting, and wait for
  a fresh decoder hook marker before reporting an active decoder as ready.
- Restore the original AVConference registration before an automatic decoder
  fallback after a failed route change.

Bridge iPhones require **iOS 15 or later** and a compatible active jailbreak.
Sequoia connections and the integrated iOS 15 keystore fallback still need
device validation.

## 0.1.0

- Guided SSH and USB bridge setup, host-key verification and certificate preparation.
- Rootless and RootHide deployment support.
- Background service with connection recovery and verified cleanup.
- Media transport and decoder compatibility adapters.
- Compact interface with Settings, appearance options and native error dialogs.
- Live logs, dated files, seven-day retention and a log-folder action.
- App version in Settings and About.
- DMG installation with an Applications shortcut and a bundled Python runtime.
- Documentation, demo images, GitHub source downloads and third-party notices.
