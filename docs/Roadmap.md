# Testing priorities

Mirra provides guided bridge setup, background support and native Mirroring
launches. Broader compatibility and long-term reliability require testing on
additional devices and hosts.

## Areas to validate

- Additional bridge models, iOS versions and jailbreak layouts.
- Additional macOS builds and Wi-Fi/video drivers.
- First-time SSH authorization and certificate issuance without a cache.
- Video quality, input latency and sustained audio/video sessions.
- Reconnection after idle periods, login, restart and sleep/wake.
- Native notification delivery and phone-app launching.

## Acceptance criteria

Build and verify packages from a clean checkout or tagged source download.
Check package manifests, signatures, runtime relocation, relevant regression tests and native
controls. For device changes, complete setup and a connected Mirroring session,
including reconnection and cleanup.

Record the configuration and observed results. Readiness reports and isolated
API tests describe their own scope; video, input and native features need
direct verification on the affected devices.
