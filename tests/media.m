// Exercise actual Objective-C hooks against recording implementations in this
// process. No phone connection, real screen request or cryptographic data.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <stdio.h>

static NSDictionary *receivedRequest;
static NSString *receivedRequestID;
static id receivedDestination, receivedOptions, receivedHandler;
static uint32_t nativeTiles, receivedFlags;

static void request_original(id object, SEL selector, NSString *requestID, NSDictionary *request,
                             id destination, id options, id handler) {
    (void)object; (void)selector;
    receivedRequestID = requestID; receivedRequest = request;
    receivedDestination = destination; receivedOptions = options; receivedHandler = handler;
}
static uint32_t tiles_original(id object, SEL selector) { (void)object; (void)selector; return nativeTiles; }
static void flags_original(id object, SEL selector, uint32_t flags) { (void)object; (void)selector; receivedFlags = flags; }
static void require(BOOL condition, NSString *message) {
    if (!condition) { fprintf(stderr, "%s\n", message.UTF8String); exit(1); }
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        BOOL expectIncompatible = argc == 3 && !strcmp(argv[2], "--expect-incompatible");
        require(argc == 2 || expectIncompatible, @"Expected adapter path");
        require(dlopen("/System/Library/PrivateFrameworks/Rapport.framework/Rapport", RTLD_NOW) != NULL, @"Load Rapport");
        require(dlopen("/System/Library/PrivateFrameworks/AVConference.framework/AVConference", RTLD_NOW) != NULL, @"Load AVConference");
        SEL requestSelector = sel_registerName("sendRequestID:request:destinationID:options:responseHandler:");
        Method request = class_getInstanceMethod(NSClassFromString(@"RPRemoteDisplaySession"), requestSelector);
        Method tiles = class_getInstanceMethod(NSClassFromString(@"VCMediaNegotiatorLocalConfiguration"), sel_registerName("tilesPerVideoFrame"));
        Method traffic = class_getInstanceMethod(NSClassFromString(@"RPStreamSession"), sel_registerName("setTrafficFlags:"));
        Method stream = class_getInstanceMethod(NSClassFromString(@"RPStreamSession"), sel_registerName("setStreamFlags:"));
        require(request && tiles && traffic && stream, @"Required methods are present");
        method_setImplementation(request, (IMP)request_original);
        method_setImplementation(tiles, (IMP)tiles_original);
        method_setImplementation(traffic, (IMP)flags_original);
        method_setImplementation(stream, (IMP)flags_original);
        require(dlopen(argv[1], RTLD_NOW | RTLD_LOCAL) != NULL, @"Load adapter");
        if (expectIncompatible) {
            require(method_getImplementation(request) == (IMP)request_original &&
                    method_getImplementation(tiles) == (IMP)tiles_original &&
                    method_getImplementation(traffic) == (IMP)flags_original &&
                    method_getImplementation(stream) == (IMP)flags_original, @"Incompatible profile partially installed hooks");
            puts("{\"passed\":true,\"unchanged_hooks\":4,\"scope\":\"Rejected changed framework ABI before installing any hook\"}");
            return 0;
        }
        NSMutableArray *cases = [NSMutableArray array];
        id destination = [NSObject new], options = [NSObject new], handler = ^{};
        for (NSString *requestID in @[@"_streamStart", @"unrelatedRequest"]) {
            for (NSString *streamID in @[@"com.apple.MediaTransport.video", @"com.apple.MediaTransport.audio", @"unrelatedStream"]) {
                for (NSNumber *type in @[@1, @3, @4]) {
                    NSDictionary *input = @{@"_streamID": streamID, @"_streamType": type,
                        @"_tf": @27, @"_streamFlags": @6, @"unrelatedMetadata": @"preserve"};
                    ((void (*)(id, SEL, NSString *, NSDictionary *, id, id, id))method_getImplementation(request))(
                        nil, requestSelector, requestID, input, destination, options, handler);
                    BOOL changes = [requestID isEqual:@"_streamStart"] && ![streamID isEqual:@"unrelatedStream"] && type.intValue == 3;
                    NSMutableDictionary *expected = [input mutableCopy];
                    if (changes) { expected[@"_streamType"] = @1; expected[@"_tf"] = @(0x10000); expected[@"_streamFlags"] = @4; }
                    require([receivedRequest isEqual:expected], @"Unexpected request translation");
                    require(receivedDestination == destination && receivedOptions == options && receivedHandler == handler,
                            @"Routing or response handler changed");
                    require(receivedRequestID == requestID, @"Request ID changed");
                    require([input[@"_tf"] isEqual:@27], @"Input request mutated");
                    [cases addObject:@{@"request": requestID, @"translated": receivedRequest}];
                }
            }
        }
        for (NSNumber *value in @[@0, @1, @2, @4, @16, @(UINT32_MAX)]) {
            nativeTiles = value.unsignedIntValue;
            uint32_t actual = ((uint32_t (*)(id, SEL))method_getImplementation(tiles))(nil, method_getName(tiles));
            require(actual == (nativeTiles > 1 ? 1 : nativeTiles), @"Unexpected tile negotiation");
            [cases addObject:@{@"native_tiles": value, @"offered_tiles": @(actual)}];
        }
        for (NSNumber *value in @[@0, @1, @4, @6, @7, @(0x10000), @(0x10001), @(UINT32_MAX)]) {
            uint32_t flags = value.unsignedIntValue;
            ((void (*)(id, SEL, uint32_t))method_getImplementation(traffic))(nil, method_getName(traffic), flags);
            require(receivedFlags == (flags == 0x10000 ? 0 : flags), @"Unexpected traffic flags");
            [cases addObject:@{@"traffic_flags": value, @"forwarded": @(receivedFlags)}];
            ((void (*)(id, SEL, uint32_t))method_getImplementation(stream))(nil, method_getName(stream), flags);
            require(receivedFlags == (flags == 6 ? 0 : flags), @"Unexpected stream flags");
            [cases addObject:@{@"stream_flags": value, @"forwarded": @(receivedFlags)}];
        }
        NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"passed": @YES, @"cases": cases}
                                                      options:NSJSONWritingSortedKeys error:NULL];
        fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout);
    }
    return 0;
}
