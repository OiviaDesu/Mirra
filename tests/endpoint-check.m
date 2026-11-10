// Device check adapted from @OiviaDesu's PR #4 (a193c167).
#define main endpoint_main
#include "../src/phone/endpoint.m"
#undef main

static int check_symbols(void) {
    if (!TARGET_OS_IPHONE) {
        fputs("This check requires an iPhone bridge\n", stderr);
        return 1;
    }
    KeyStore keys = {0};
    NSArray<NSString *> *missing = load_keystore(&keys, keystore_library(), dlsym, mapped_keystore_symbol);
    if (missing.count) {
        fputs("Required keystore symbols unavailable\n", stderr);
        return 1;
    }
    // Newly allocated, unsaved bag only. Never use the device's system keybag.
    uint8_t password[32] = {0};
    if (SecRandomCopyBytes(kSecRandomDefault, sizeof(password), password)) return 1;
    int bag = -1;
    int created = keys.create_bag(password, sizeof(password), 0, &bag);
    volatile uint8_t *secret = password;
    for (size_t i = 0; i < sizeof(password); i++) secret[i] = 0;
    printf("create_bag=%d\n", created);
    if (created || bag < 0) {
        if (bag >= 0) (void)keys.unload_bag(bag);
        return 1;
    }
    void *blob = NULL;
    size_t size = 0;
    int signed_key = keys.create_key(bag, 9, &blob, &size);
    bool key_ok = signed_key == 0 && blob && size;
    printf("create_signing_key=%d bytes=%zu\n", signed_key, size);
    clear_blob(blob, size);
    int unloaded = keys.unload_bag(bag); // Always release the owned bag, including key failure.
    printf("unload_bag=%d\n", unloaded);
    if (!key_ok || unloaded) return 1;
    // One params allocation; process exit reclaims it. No persistent state.
    void *p = keys.params_create(NULL, 0);
    int64_t flag = 4;
    const unsigned char sample[] = {1, 2, 3};
    const void *encoded = NULL;
    size_t length = 0;
    if (!p || keys.params_number(p, 20, &flag) || keys.params_data(p, 21, sample, sizeof(sample)) ||
        keys.params_der(p, &encoded, &length) || !encoded || !length) return 1;
    printf("params_roundtrip=ok bytes=%zu\nPASS\n", length);
    return 0;
}

int main(void) {
    @autoreleasepool { return check_symbols(); }
}
