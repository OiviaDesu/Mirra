// Synthetic Mach-O metadata only; no AKS functions or phone connection.
#define MIRRA_SYMBOL_FIXTURES 1
#define main unused_endpoint_main
#include "../src/phone/endpoint.m"
#undef main
#include <stdio.h>
#include <stdlib.h>

static unsigned checks;
#define CHECK(value) do { checks++; if (!(value)) { \
    fprintf(stderr, "line %d: %s\n", __LINE__, #value); exit(1); } } while (0)

typedef struct {
    struct mach_header_64 header;
    struct uuid_command uuid;
    struct segment_command_64 segment;
    struct section_64 section;
} Fixture;

static Fixture fixture(unsigned image) {
    Fixture f = {0};
    f.header = (struct mach_header_64){.magic = MH_MAGIC_64, .cputype = CPU_TYPE_ARM64,
        .cpusubtype = CPU_SUBTYPE_ARM64_ALL, .ncmds = 2, .sizeofcmds = sizeof(f) - sizeof(f.header)};
    f.uuid.cmd = LC_UUID; f.uuid.cmdsize = sizeof(f.uuid);
    memcpy(f.uuid.uuid, keystore_images[image].uuid, sizeof(f.uuid.uuid));
    f.segment = (struct segment_command_64){.cmd = LC_SEGMENT_64,
        .cmdsize = sizeof(f.segment) + sizeof(f.section), .vmaddr = 0x100000,
        .vmsize = 0x80000, .initprot = VM_PROT_READ | VM_PROT_EXECUTE, .nsects = 1};
    strcpy(f.segment.segname, "__TEXT");
    strcpy(f.section.segname, "__TEXT"); strcpy(f.section.sectname, "__text");
    f.section.addr = 0x101000; f.section.size = 0x7f000;
    f.section.flags = S_ATTR_PURE_INSTRUCTIONS;
    return f;
}

static void *resolve_fixture(Fixture f, const MappedSymbol *symbol) {
    return image_symbol(&f, sizeof(f), 0x200000, 0x100000, symbol);
}

int main(void) {
    CHECK(symbol_map_supported("19H422", "arm64"));
    CHECK(!symbol_map_supported("19H421", "arm64")); CHECK(!symbol_map_supported("19H422x", "arm64"));
    CHECK(!symbol_map_supported(NULL, "arm64")); CHECK(!find_symbol(NULL));
    CHECK(!find_symbol("aks_unknown"));
    CHECK(!symbol_map_supported("19H422", "arm64e"));
    CHECK(!symbol_map_supported("19H422", "x86_64"));
    CHECK(!symbol_map_supported("19H422", NULL));
    CHECK(!mapped_keystore_symbol("aks_create_bag")); // Host never enables the fallback.
    for (size_t i = 0; i < sizeof(keystore_symbols) / sizeof(*keystore_symbols); i++) {
        const MappedSymbol *s = &keystore_symbols[i];
        Fixture f = fixture(s->image);
        CHECK(find_symbol(s->name) == s);
        CHECK(resolve_fixture(f, s) == (void *)(0x200000 + s->offset));
        CHECK(image_symbol(&f, sizeof(f), 0x80000, -0x80000, s) == (void *)(0x80000 + s->offset));
        f.uuid.uuid[0] ^= 1;
        CHECK(!resolve_fixture(f, s));
    }
    const MappedSymbol *s = find_symbol("aks_create_bag");
    Fixture f = fixture(0);
    for (size_t n = 0; n < sizeof(f); n++)
        CHECK(!image_symbol(&f, n, 0x200000, 0x100000, s));
    CHECK(!image_symbol(NULL, sizeof(f), 0x200000, 0x100000, s));
    CHECK(!image_symbol(&f, sizeof(f), 0x200000, 0x100000, NULL));
    CHECK(!image_symbol(&f, sizeof(f), UINTPTR_MAX - 3, 0, s));
    CHECK(!image_symbol(&f, sizeof(f), 0x200001, 0x100000, s));
#define REJECT(change) do { f = fixture(0); change; CHECK(!resolve_fixture(f, s)); } while (0)
    REJECT(f.header.magic = MH_CIGAM_64);
    REJECT(f.header.cputype = CPU_TYPE_X86_64);
    REJECT(f.header.cpusubtype = CPU_SUBTYPE_ARM64E);
    REJECT(f.header.ncmds = 1);
    REJECT(f.header.ncmds = 3);
    REJECT(f.header.sizeofcmds = UINT32_MAX);
    REJECT(f.uuid.cmdsize = 0);
    REJECT(f.uuid.cmdsize = UINT32_MAX);
    REJECT(f.uuid.cmdsize -= 1);
    REJECT(f.uuid.cmd = LC_CODE_SIGNATURE);
    REJECT(f.segment.cmdsize -= sizeof(f.section));
    REJECT(f.segment.nsects = UINT32_MAX);
    REJECT(f.segment.initprot = VM_PROT_READ);
    REJECT(strcpy(f.segment.segname, "__DATA"));
    REJECT(f.segment.vmaddr = UINT64_MAX - 2);
    REJECT(f.segment.vmsize = 3);
    REJECT(f.section.addr = 0x100000 + s->offset + 4);
    REJECT(f.section.size = 3);
    REJECT(f.section.addr = 0x100000; f.section.size = s->offset + 3);
    REJECT(f.section.addr = UINT64_MAX - 2);
    REJECT(f.section.flags = 0);
    REJECT(strcpy(f.section.sectname, "__cstring"));
    REJECT(strcpy(f.section.segname, "__DATA"));
    uintptr_t address;
    CHECK(!slid_address(0, -1, &address));
    CHECK(!slid_address(UINT64_MAX, 1, &address));
    CHECK(!slid_address(0, INTPTR_MIN, &address));
    CHECK(slid_address(0x100000, -0x80000, &address) && address == 0x80000);
    printf("PASS: %u keystore symbol-map guard checks (no private functions executed)\n", checks);
    return 0;
}
