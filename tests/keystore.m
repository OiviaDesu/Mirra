// Compile the real shim functions without installing dyld interpositions.
// Only reset_all's native call is replaced; no Mac or phone AKS reset occurs.
#define DYLD_INTERPOSE(REPLACEMENT, ORIGINAL) \
    __attribute__((used)) static const void *probe_use_##ORIGINAL = (const void *)(REPLACEMENT);
#define aks_remote_session_reset_all probe_native_reset_all
#include "../src/mirroring/keystore.m"

static int native_resets;
int probe_native_reset_all(void) { native_resets++; return 73; }

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 2) return 64;
        if (!strcmp(argv[1], "reset")) {
            int result = mirra_reset_all();
            printf("{\"status\":%d,\"native_resets\":%d}\n", result, native_resets);
        } else if (!strcmp(argv[1], "session")) {
            NSData *local = [NSMutableData dataWithLength:512];
            [local_keys addObject:encoded(local.bytes, local.length)];
            int session = -1;
            int result = mirra_session(0, 2, local.bytes, local.length, "remote", 6, NULL, 0, &session);
            printf("{\"status\":%d,\"session\":%d}\n", result, session);
        } else return 64;
    }
    return 0;
}
