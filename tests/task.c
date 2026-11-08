// Read-only capability probe for an already running, original Mirroring app.
// Acquiring a task port does not load code, suspend threads or change memory.
#include <errno.h>
#include <libproc.h>
#include <mach/mach.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 2) return 64;
    char *end = NULL;
    errno = 0;
    long value = strtol(argv[1], &end, 10);
    if (errno || !end || *end || value <= 0 || value > 0x7fffffff) return 64;
    pid_t pid = (pid_t)value;
    char path[PROC_PIDPATHINFO_MAXSIZE] = {0};
    if (proc_pidpath(pid, path, sizeof(path)) <= 0 ||
        strcmp(path, "/System/Applications/iPhone Mirroring.app/Contents/MacOS/iPhone Mirroring")) {
        fprintf(stderr, "Target must be the running original system app.\n");
        return 65;
    }
    mach_port_t task = MACH_PORT_NULL;
    kern_return_t result = task_for_pid(mach_task_self(), pid, &task);
    printf("{\"pid\":%d,\"task_for_pid_result\":%d,\"task_port_acquired\":%s,"
           "\"target_modified\":false}\n", pid, result, result == KERN_SUCCESS ? "true" : "false");
    if (task != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), task);
    return result == KERN_SUCCESS ? 0 : 1;
}
