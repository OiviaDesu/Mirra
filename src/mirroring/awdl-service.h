#ifndef MIRRA_AWDL_SERVICE_H
#define MIRRA_AWDL_SERVICE_H
#include <stdbool.h>

// Repair the known OCLP RemoteScreen constant stub in this process, if present.
// Returns true only when the narrowly guarded reservation hook is installed.
bool MirraRepairAWDLService(void);

#endif
