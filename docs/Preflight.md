# Connection checks

Before opening iPhone Mirroring, Mirra checks the configured bridge's process
lifetime, endpoint health and launch support. Status must belong to the current
service session. If ready, Mirra opens Apple's app; otherwise, a native dialog
offers recovery guidance and access to logs.

The health observation does not change service state. Unavailable observations
remain unknown rather than authorizing a restart.

Apple's app handles target discovery, radio requirements, account checks and
remote unlock. Mirra does not add a target confirmation or Bluetooth-power gate.
Bridge readiness and a connected target screen are different states.

For setup or connection failures, use **Show Log** and inspect the failing
attempt. See [App guide](App.md) and [Background support](Service.md).
