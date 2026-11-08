// Compile the endpoint's actual cleanup helper. Endpoint main is never run,
// so no device, keybag, certificate or real AKS reset is accessed by this test.
#define main unused_endpoint_main
#include "../src/phone/endpoint.m"
#undef main

static int reset_calls;
static int reset_status;
static int last_handle;
static int record_reset(int handle) {
    reset_calls++;
    last_handle = handle;
    return reset_status;
}

static void *fake_symbol(void *library, const char *name) {
    (void)library;
    return strcmp(name, "aks_remote_session") == 0 ? NULL : (void *)record_reset;
}

int main(void) {
    @autoreleasepool {
        NSMutableSet *owned = [NSMutableSet setWithArray:@[@17, @91]];
        NSDictionary *reply = reset_owned_session(owned, @8, record_reset);
        if (![reply[@"error"] isEqual:@"session_not_owned"] || reset_calls || owned.count != 2) return 1;
        reset_status = -123;
        reply = reset_owned_session(owned, @17, record_reset);
        if ([reply[@"status"] intValue] != -123 || reset_calls != 1 || last_handle != 17 ||
            ![owned containsObject:@17] || ![owned containsObject:@91]) return 2;
        reset_status = 0;
        reply = reset_owned_session(owned, @17, record_reset);
        if ([reply[@"status"] intValue] || reset_calls != 2 || [owned containsObject:@17] ||
            ![owned containsObject:@91]) return 3;
        reply = reset_owned_session(owned, @17, record_reset);
        if (![reply[@"error"] isEqual:@"session_not_owned"] || reset_calls != 2) return 4;
        NSMutableArray *missing = [NSMutableArray array];
        if (required_symbol((void *)1, "aks_remote_session", missing, fake_symbol) != NULL ||
            ![missing isEqual:@[@"aks_remote_session"]]) return 5;
        if (required_symbol((void *)1, "aks_remote_session_reset", missing, fake_symbol) == NULL || missing.count != 1) return 6;
        if (required_symbol(NULL, "aks_create_bag", missing, fake_symbol) != NULL || missing.count != 2) return 7;
        puts("{\"cases\":7,\"passed\":true,\"real_aks_accessed\":false}");
    }
    return 0;
}
