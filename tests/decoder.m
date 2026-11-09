// Exercise the production hook without starting AVConference or decoding video.
#define VCPDecompressionSessionCreate fixtureCreate
#include "../src/mirroring/decoder-route.m"
#undef VCPDecompressionSessionCreate
#include <assert.h>

static NSDictionary *received;
static CFDictionaryRef receivedAttributes;
static CMVideoFormatDescriptionRef receivedFormat;
static const VTDecompressionOutputCallbackRecord *receivedCallback;
static VTDecompressionSessionRef *receivedSession;
static unsigned calls;

OSStatus fixtureCreate(CFAllocatorRef allocator, CMVideoFormatDescriptionRef format,
    CFDictionaryRef specification, CFDictionaryRef attributes,
    const VTDecompressionOutputCallbackRecord *callback, VTDecompressionSessionRef *session) {
    (void)allocator;
    calls++;
    received = [(__bridge NSDictionary *)specification copy];
    receivedFormat = format;
    receivedAttributes = attributes;
    receivedCallback = callback;
    receivedSession = session;
    return -123; // Preserve the original decoder's result, including errors.
}

static CMVideoFormatDescriptionRef formatFor(BOOL p3) {
    // Valid 16x16 baseline SPS with VUI colour primaries 12 or 1, plus PPS.
    const uint8_t wide[] = {0x67,0x42,0x00,0x1e,0xf4,0xf4,0xdc,0x30,0x34,0x04,0x08};
    const uint8_t standard[] = {0x67,0x42,0x00,0x1e,0xf4,0xf4,0xdc,0x04,0x34,0x04,0x08};
    const uint8_t pps[] = {0x68,0xce,0x3c,0x80};
    const uint8_t *sets[] = {p3 ? wide : standard, pps};
    const size_t sizes[] = {sizeof wide, sizeof pps};
    CMVideoFormatDescriptionRef format = NULL;
    assert(!CMVideoFormatDescriptionCreateFromH264ParameterSets(NULL, 2, sets, sizes, 4, &format));
    assert(primaries(format) == (p3 ? 12 : 1));
    return format;
}

static void check(CMVideoFormatDescriptionRef format, NSDictionary *options, BOOL removed) {
    NSDictionary *before = [options copy];
    NSDictionary *attributes = @{ @"PreserveThisAttribute": @YES };
    VTDecompressionOutputCallbackRecord callback = {NULL, NULL};
    VTDecompressionSessionRef session = NULL;
    unsigned previousCalls = calls;
    assert(routedCreate(NULL, format, (__bridge CFDictionaryRef)options,
        (__bridge CFDictionaryRef)attributes, &callback, &session) == -123);
    assert(calls == previousCalls + 1);
    assert(receivedFormat == format && receivedAttributes == (__bridge CFDictionaryRef)attributes);
    assert(receivedCallback == &callback && receivedSession == &session);
    assert(!options || [options isEqual:before]);
    NSMutableDictionary *expected = [options mutableCopy];
    if (removed) [expected removeObjectForKey:@"NegotiationDetails"];
    assert((!received && !expected) || [received isEqual:expected]);
}

int main(void) {
    @autoreleasepool {
        CMVideoFormatDescriptionRef p3 = formatFor(YES), standard = formatFor(NO), hevc = NULL, unknown = NULL;
        assert(!CMVideoFormatDescriptionCreate(NULL, kCMVideoCodecType_HEVC, 16, 16, NULL, &hevc));
        assert(!CMVideoFormatDescriptionCreate(NULL, kCMVideoCodecType_H264, 16, 16, NULL, &unknown));
        NSDictionary *fls = @{ @"NegotiationDetails": @"FLS", @"NegotiationDetailsBitField": @7,
            @"NegotiatedFLB": @YES, @"OtherOption": @"preserved" };
        setenv("MIRRA_NO_FLS", "1", 1);
        setenv("MIRRA_DECODER_POLICY", "fls_p3", 1);
        check(p3, fls, YES);
        check(standard, fls, NO);
        check(unknown, fls, NO);
        check(hevc, fls, NO);
        check(p3, @{ @"NegotiationDetails": @"OTHER" }, NO);
        check(p3, @{ @"NegotiationDetails": @"decoder=FLS;options=1" }, YES);
        check(p3, @{ @"NegotiationDetails": @42 }, NO);
        check(p3, @{ @"OtherOption": @1 }, NO);
        check(p3, nil, NO);
        setenv("MIRRA_DECODER_POLICY", "unknown-policy", 1);
        check(p3, fls, NO);
        setenv("MIRRA_DECODER_POLICY", "all_h264", 1);
        check(standard, fls, YES);
        check(p3, @{ @"NegotiationDetails": @"OTHER" }, YES);
        unsetenv("MIRRA_DECODER_POLICY");
        check(p3, fls, YES);
        unsetenv("MIRRA_NO_FLS");
        check(p3, fls, NO);
        CFRelease(p3); CFRelease(standard); CFRelease(hevc); CFRelease(unknown);
        printf("Decoder hook: %u cases passed\n", calls);
    }
    return 0;
}
