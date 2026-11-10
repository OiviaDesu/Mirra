# Installation

## Install the app

1. Get `Mirra-v<version>.dmg` from [Releases](https://github.com/eiyuumiru/Mirra/releases)
   or the maintainer. Verify its checksum when one is supplied.
2. Open the image and drag **Mirra.app** onto **Applications**.
3. Wait for copying to finish, eject the image and open **Mirra** from Applications.
4. Follow bridge setup and choose **Connect**.

Python and the required helpers are included. Check
[Compatibility](../README.md#compatibility) before installing.

GitHub's **Source code** downloads are for [building the app](Build.md).

## macOS security prompts

The current packaging process uses ad-hoc signing and does not notarize the app.
If macOS blocks a trusted copy, **System Settings → Privacy & Security** may offer
**Open Anyway** after an attempted launch. Follow
[Apple's security guidance](https://support.apple.com/en-us/102445);
do not disable Gatekeeper globally.

## Set up the bridge

Use a separate jailbroken iPhone running **iOS 15 or later**, with OpenSSH and
`ldid`. Mirra verifies its SSH
identity, installs a helper and prepares the bridge's certificate. The target
iPhone is selected through Apple's iPhone Mirroring app.

See the [app guide](App.md) for guided setup, [Bridge compatibility](Bridges.md)
for jailbreak requirements, or [Bridge setup](Setup.md) for the CLI alternative.

Wi-Fi and video drivers are installed separately. Follow the relevant
[MtkWireless](https://github.com/Dino-VN/MtkWireless) or
[HighDelay NVIDIA driver](https://github.com/HighDelay/nvidia-macos-driver/releases)
documentation when using those projects.

## Import saved setup

In Settings, stop support and choose **Import…**. Select the saved `ready.json`.
Its SSH files and certificate must still exist and belong to the intended
bridge. Installation copies them into Mirra's private service package.

## Update the app

Quit Mirra, open the new DMG and drag Mirra.app to Applications, replacing the
earlier copy when Finder asks. Eject the image and open the updated app. Keep
one installed copy to avoid duplicate Launchpad entries.

Saved setup and logs live separately in Application Support. When offered,
**Update and connect** updates background support using the existing bridge,
keys and certificate. Command-line service updates are described in
[Background support](Service.md).

## Troubleshooting

- **Host or framework rejected:** inspect the compatibility report; see [Media](Media.md).
- **SSH host-key mismatch:** verify the bridge's identity and configured fingerprint.
- **Forwarding port occupied:** stop the competing proxy or use the existing verified forwarder.
- **Helper cannot launch:** check signing, permissions and [jailbreak paths](Bridges.md).
- **Certificate mismatch:** use the bridge profile and its matching certificate.
- **Bridge ready but no screen:** check the target's lock state and Apple's connection error.

Use Settings to open the logs from the failing attempt.
