# App guide

Mirra prepares and maintains the bridge connection. **Connect** opens Apple's
iPhone Mirroring; **Settings** contains setup, service controls and logs.

## First launch

Use a separate jailbroken iPhone with OpenSSH and `ldid` as the bridge. Choose
Wi-Fi SSH or a USB device, then enter the account and the phone's SSH port.

Setup guides you through host-key verification, SSH authorization, helper
deployment and certificate preparation. Compare the SSH fingerprint with a
trusted value before authorizing access. Enter the password when prompted;
Mirra saves its SSH key rather than the password.

**Prepare connection** installs and signs the helper, retrieves the bridge's
certificate or requests one when missing, and checks a matching attested key.
**Finish setup** activates the bridge and enables background startup at login.
An existing Mirroring session closes when switching bridges.

Completed setup steps are saved and can resume after reopening. For command-line
setup and fingerprint instructions, see [Bridge setup](Setup.md).

## Connect

| State | Action |
| --- | --- |
| No saved setup | Set up Mirra opens or resumes setup. |
| Bridge ready | Connect checks bridge health and opens Apple's app. |
| Service stopped | Connect starts support and waits for readiness. |
| Support update available | Update and connect reuses the saved bridge profile. |
| Connecting or restoring | Wait for the active operation to finish. |
| Status unavailable | Check again refreshes the observation. |

Keep the target iPhone locked and nearby with Wi-Fi and Bluetooth enabled.
Complete authentication in Apple's window. Bridge readiness describes the
backend; the Mirroring window shows whether the target connection succeeded.

## Settings

- **Set up Mirra** opens setup. **Change phone** creates another bridge profile.
- **Import…** loads a saved `ready.json` profile whose access files still exist.
  Stop support before changing the installed bridge.
- **Start / Stop** control background support. Closing Mirra leaves it running.
  Stopping retains login registration; the [service CLI](Service.md) can remove it.
- **Refresh** updates service status; **Show details** opens the full result.
- **Connection help** explains requirements and recovery steps.
- **System / Light / Dark** select appearance. The preference is remembered.
- **Show log** and **Open log folder** provide diagnostic access.

Settings and About display the app version. You can copy the version from Settings
when reporting an issue.

## Navigation and errors

Home, Settings, help and setup share a window. **Back** preserves the current
form. Longer content scrolls, and standard text-editing shortcuts are available.
Errors use native dialogs with recovery instructions and an optional **Show Log**
action. Leaving Settings collapses the log viewer.

Status refreshes run in the background and pause during setup changes. Results
collected before a service change are discarded. Actions that change setup or
support wait for the current operation to finish.

## Logs

Logs capture actual setup commands, SSH output, helper results, service activity
and available iPhone Mirroring/decoder messages. Capture continues while the
viewer is closed. Passwords and key/certificate contents are redacted.

**Show log** expands the viewer; **Hide log** returns to the compact window.
**Pause / Resume** control the displayed stream, **Copy** copies visible entries,
and **Verbose** includes routine debug messages. These controls do not stop the
connection.

**Open log folder** opens `~/Library/Application Support/Mirra/logs` in Finder.
Dated files use the local calendar date:

| Filename | Contents |
| --- | --- |
| `setup-YYYY-MM-DD.log` | Setup, SSH and certificate operations |
| `control-YYYY-MM-DD.log` | Service commands |
| `app-YYYY-MM-DD.log` | App actions and status changes |
| `native-YYYY-MM-DD.log` | Captured macOS Mirroring messages |

Mirra keeps seven days of dated logs. Each file rotates at 2 MiB with one backup.
Older dated files and backups are removed on startup and at date changes while
the app is open. Service/session diagnostics use the
[background service's retention rules](Service.md#diagnostic-storage).

Review logs before sharing; see [Security reports](../SECURITY.md).

## Storage

Private profiles and service files live under `~/Library/Application Support/Mirra`.
Ownership records live under `~/Library/Caches/Mirra`. The service has its own
Python runtime, so it can continue after the app is moved or closed.

Check [Compatibility](../README.md#compatibility) before installing on another
host or changing the bridge.
