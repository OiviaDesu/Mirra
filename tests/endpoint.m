// Compile the endpoint's actual cleanup helper. Endpoint main is never run,
// so no device, keybag, certificate or real AKS reset is accessed by this test.
#define main unused_endpoint_main
#include "../src/phone/endpoint.m"
#undef main

static int reset_calls;
static int reset_status;
static int last_handle;
static int symbol_calls, fallback_calls;
static int record_reset(int handle) {
    reset_calls++;
    last_handle = handle;
    return reset_status;
}

static void *fake_symbol(void *library, const char *name) {
    (void)library;
    symbol_calls++;
    return strcmp(name, "aks_remote_session") == 0 ? NULL : (void *)record_reset;
}

static void *fake_fallback(const char *name) {
    fallback_calls++;
    return strcmp(name, "aks_remote_session") == 0 ? (void *)fake_fallback : NULL;
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
        if (required_symbol((void *)1, "aks_remote_session", missing, fake_symbol, NULL) != NULL ||
            ![missing isEqual:@[@"aks_remote_session"]]) return 5;
        if (required_symbol((void *)1, "aks_remote_session_reset", missing, fake_symbol, NULL) == NULL || missing.count != 1) return 6;
        if (required_symbol(NULL, "aks_create_bag", missing, fake_symbol, NULL) != NULL || missing.count != 2) return 7;
        KeyStore keys = {0};
        symbol_calls = fallback_calls = 0;
        missing = load_keystore(&keys, (void *)1, fake_symbol, fake_fallback);
        if (missing.count || symbol_calls != 18 || fallback_calls != 1 ||
            (void *)keys.reset_session != (void *)record_reset ||
            (void *)keys.remote_session != (void *)fake_fallback) return 8;
        // Unsupported maps retain the original error list and available APIs.
        missing = load_keystore(&keys, (void *)1, fake_symbol, mapped_keystore_symbol);
        if (![missing isEqual:@[@"aks_remote_session"]] || keys.remote_session ||
            (void *)keys.reset_session != (void *)record_reset) return 9;
        symbol_calls = 0;
        missing = load_keystore(&keys, NULL, fake_symbol, NULL);
        if (missing.count != 18 || symbol_calls || keys.reset_session || keys.remote_session) return 10;
        fallback_calls = 0;
        missing = load_keystore(&keys, NULL, fake_symbol, fake_fallback);
        if (missing.count != 17 || symbol_calls || fallback_calls != 18 ||
            (void *)keys.remote_session != (void *)fake_fallback || keys.reset_session) return 11;
        puts("{\"cases\":11,\"passed\":true,\"real_aks_accessed\":false}");
    }
    return 0;
}
