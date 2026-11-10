// Offsets and UUIDs by @OiviaDesu, Mirra PR #4, commit a193c167.
// Image-relative offsets, verified only for iOS 15.8.8 / 19H422 / arm64.
// Never reuse a map on a different build or an image with a different UUID.
static const char keystore_build[] = "19H422";

typedef struct {
    const char *path;
    unsigned char uuid[16];
} MappedImage;

typedef struct {
    const char *name;
    unsigned image;
    uintptr_t offset;
} MappedSymbol;

static const MappedImage keystore_images[] = {
    {"/System/Library/PrivateFrameworks/MobileKeyBag.framework/MobileKeyBag",
     {0x69,0x3c,0x03,0x17,0xa8,0x20,0x3d,0x3f,0xb4,0x7c,0x8a,0xb6,0x55,0x5e,0x13,0x71}},
    {"/System/Library/Frameworks/CryptoTokenKit.framework/CryptoTokenKit",
     {0x8f,0x3f,0x79,0xe7,0x92,0x61,0x3f,0x8e,0x99,0xdb,0x06,0x0f,0x61,0x00,0x9b,0x79}},
};

static const MappedSymbol keystore_symbols[] = {
    {"aks_create_bag", 0, 0x22a00},
    {"aks_unload_bag", 0, 0x22de8},
    {"aks_create_signing_key", 0, 0x24fac},
    {"aks_create_signing_key_with_params", 0, 0x250f4},
    {"aks_sign_signing_key", 0, 0x253d8},
    {"aks_remote_session", 0, 0x25588},
    {"aks_remote_session_step", 0, 0x2587c},
    {"aks_remote_session_reset", 0, 0x25f08},
    {"aks_remote_session_token", 0, 0x258dc},
    {"aks_remote_peer_confirm", 0, 0x258c4},
    {"aks_remote_peer_setup", 0, 0x25894},
    {"aks_validate_local_key", 0, 0x256a8},
    {"aks_remote_peer_get_state", 0, 0x25f54},
    {"aks_remote_peer_drop", 0, 0x26134},
    {"aks_params_create", 1, 0x3b8d4},
    {"aks_params_set_data", 1, 0x3bb34},
    {"aks_params_set_number", 1, 0x3bac0},
    {"aks_params_get_der", 1, 0x3bc2c},
};
