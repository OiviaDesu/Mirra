// Harmless constructor for proving normal LaunchServices supplied the adapter.
#include <os/log.h>
#include <stdlib.h>
#include <unistd.h>
__attribute__((constructor)) static void loaded(void) {
    os_log_t log = os_log_create("local.mirra.probe", "Marker");
    os_log(log, "MIRRA_NATIVE_LAUNCH_MARKER pid=%d marker_present=%d", getpid(), getenv("MIRRA_LAUNCH_PROBE") != NULL);
}
