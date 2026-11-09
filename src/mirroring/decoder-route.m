// Session-only H264 decoder options hook. No compressed frames are recorded.
#import <Foundation/Foundation.h>
#import <VideoToolbox/VideoToolbox.h>
#include <dlfcn.h>
#include <os/log.h>
#include <stdatomic.h>
#include <unistd.h>

extern OSStatus VCPDecompressionSessionCreate(CFAllocatorRef, CMVideoFormatDescriptionRef,
    CFDictionaryRef, CFDictionaryRef, const VTDecompressionOutputCallbackRecord *, VTDecompressionSessionRef *);
static _Atomic unsigned creates;
static os_log_t routeLog;
typedef struct { const uint8_t *data; size_t length, position; BOOL valid; } Bits;
static uint32_t readBits(Bits *bits, unsigned count) {
    if (count > 32 || bits->position + count > bits->length * 8) { bits->valid = NO; return 0; }
    uint32_t result = 0;
    for (unsigned i = 0; i < count; ++i) {
        size_t p = bits->position++;
        result = (result << 1) | ((bits->data[p / 8] >> (7 - p % 8)) & 1);
    }
    return result;
}
static uint32_t ue(Bits *bits) {
    unsigned zeros = 0;
    while (bits->valid && !readBits(bits, 1)) {
        if (++zeros > 31) { bits->valid = NO; return 0; }
    }
    return bits->valid ? ((1U << zeros) - 1) + readBits(bits, zeros) : 0;
}
static int64_t se(Bits *bits) { uint32_t n = ue(bits); return n & 1 ? (int64_t)(n + 1ULL) / 2 : -(int64_t)n / 2; }
static int primaries(CMVideoFormatDescriptionRef format) {
    const uint8_t *nal = NULL; size_t length = 0;
    if (CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, 0, &nal, &length, NULL, NULL) ||
        !nal || length < 5 || length > 65536 || (nal[0] & 31) != 7) return -1;
    NSMutableData *rbsp = [NSMutableData dataWithLength:length];
    uint8_t *bytes = rbsp.mutableBytes; size_t output = 0;
    for (size_t i = 1; i < length; ++i) {
        if (i >= 3 && nal[i] == 3 && nal[i - 2] == 0 && nal[i - 1] == 0) {
            if (i + 1 >= length || nal[i + 1] > 3) return -1;
            continue;
        }
        bytes[output++] = nal[i];
    }
    Bits bits = {bytes, output, 0, YES};
    uint32_t profile = readBits(&bits, 8); readBits(&bits, 16); ue(&bits);
    if (profile == 100 || profile == 110 || profile == 122 || profile == 244 || profile == 44 ||
        profile == 83 || profile == 86 || profile == 118 || profile == 128 || profile == 138 ||
        profile == 139 || profile == 134 || profile == 135) {
        uint32_t chroma = ue(&bits);
        if (chroma > 3) return -1;
        if (chroma == 3) readBits(&bits, 1);
        ue(&bits); ue(&bits); readBits(&bits, 1);
        if (readBits(&bits, 1)) {
            for (unsigned i = 0; i < (chroma == 3 ? 12U : 8U); ++i) {
                if (readBits(&bits, 1)) {
                    int64_t last = 8, next = 8;
                    for (unsigned j = 0; j < (i < 6 ? 16U : 64U); ++j) {
                        if (next) next = ((last + se(&bits)) % 256 + 256) % 256;
                        if (next) last = next;
                    }
                }
            }
        }
    }
    ue(&bits); uint32_t order = ue(&bits);
    if (order == 0) ue(&bits);
    else if (order == 1) {
        readBits(&bits, 1); se(&bits); se(&bits); uint32_t cycle = ue(&bits);
        if (cycle > 256) return -1;
        for (unsigned i = 0; i < cycle; ++i) se(&bits);
    } else if (order > 2) return -1;
    ue(&bits); readBits(&bits, 1); ue(&bits); ue(&bits);
    if (!readBits(&bits, 1)) readBits(&bits, 1);
    readBits(&bits, 1);
    if (readBits(&bits, 1)) for (unsigned i = 0; i < 4; ++i) ue(&bits);
    if (!readBits(&bits, 1)) return -1;
    if (readBits(&bits, 1) && readBits(&bits, 8) == 255) { readBits(&bits, 16); readBits(&bits, 16); }
    if (readBits(&bits, 1)) readBits(&bits, 1);
    if (!readBits(&bits, 1)) return -1;
    readBits(&bits, 4);
    if (!readBits(&bits, 1)) return -1;
    int value = (int)readBits(&bits, 8);
    return bits.valid ? value : -1;
}
static void record(const char *message) {
    fprintf(stderr, "%s\n", message); fflush(stderr);
    if (routeLog) os_log_with_type(routeLog, OS_LOG_TYPE_DEFAULT, "%{public}s", message);
}
static OSStatus routedCreate(CFAllocatorRef allocator, CMVideoFormatDescriptionRef format,
    CFDictionaryRef specification, CFDictionaryRef attributes,
    const VTDecompressionOutputCallbackRecord *callback, VTDecompressionSessionRef *session) {
    const char *enabled = getenv("MIRRA_NO_FLS");
    if (!enabled || strcmp(enabled, "1") || !format ||
        CMFormatDescriptionGetMediaSubType(format) != kCMVideoCodecType_H264 || !specification ||
        CFGetTypeID(specification) != CFDictionaryGetTypeID())
        return VCPDecompressionSessionCreate(allocator, format, specification, attributes, callback, session);
    NSDictionary *original = (__bridge NSDictionary *)specification;
    NSMutableDictionary *adapted = nil;
    id details = original[@"NegotiationDetails"];
    BOOL fls = [details isKindOfClass:NSString.class] &&
        [(NSString *)details rangeOfString:@"FLS"].location != NSNotFound;
    int colourPrimaries = primaries(format);
    const char *policy = getenv("MIRRA_DECODER_POLICY");
    BOOL automatic = policy && !strcmp(policy, "fls_p3");
    BOOL knownPolicy = !policy || !strcmp(policy, "all_h264") || automatic;
    if (details && knownPolicy && (!automatic || (fls && colourPrimaries == 12))) {
        adapted = [original mutableCopy];
        [adapted removeObjectForKey:@"NegotiationDetails"];
    }
    unsigned index = atomic_fetch_add(&creates, 1) + 1;
    OSStatus status = VCPDecompressionSessionCreate(allocator, format,
        adapted ? (__bridge CFDictionaryRef)adapted : specification, attributes, callback, session);
    typedef OSStatus (*CopyProperty)(VTDecompressionSessionRef, CFStringRef, CFAllocatorRef, CFTypeRef *);
    CopyProperty copy = (CopyProperty)dlsym(RTLD_DEFAULT, "VCPDecompressionSessionCopyProperty");
    CFTypeRef hardware = NULL; OSStatus query = -1; int usingHardware = -1;
    if (!status && session && *session && copy) {
        query = copy(*session, kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder, NULL, &hardware);
        if (!query && hardware && CFGetTypeID(hardware) == CFBooleanGetTypeID())
            usingHardware = CFBooleanGetValue(hardware) ? 1 : 0;
        if (hardware) CFRelease(hardware);
    }
    CMVideoDimensions size = CMVideoFormatDescriptionGetDimensions(format);
    char message[512];
    snprintf(message, sizeof message,
        "MIRRA_DECODER_ROUTE create=%u removed=%d had_fls=%d colour_primaries=%d width=%d height=%d status=%d hardware_query=%d using_hardware=%d secondary_bitfield=%d secondary_flb=%d",
        index, adapted != nil, fls, colourPrimaries, size.width, size.height, (int)status,
        (int)query, usingHardware, original[@"NegotiationDetailsBitField"] != nil, original[@"NegotiatedFLB"] != nil);
    record(message);
    return status;
}
__attribute__((constructor)) static void ready(void) {
    const char *enabled = getenv("MIRRA_NO_FLS");
    if (!enabled || strcmp(enabled, "1")) return;
    routeLog = os_log_create("local.mirra.decoder-test", "decoder");
    char message[128]; snprintf(message, sizeof message, "MIRRA_DECODER_HOOK_READY pid=%d version=1", getpid()); record(message);
}
__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement, *original;
} interpositions[] = {{(const void *)routedCreate, (const void *)VCPDecompressionSessionCreate}};
