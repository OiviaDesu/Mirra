#import "profile.h"
#include <os/log.h>

BOOL MirroringInstallMediaProfile(void) {
    // Validate every required hook before modifying any implementation.
    if (![MirroringMediaCompatibilityReport()[@"compatible"] boolValue]) return NO;
    Method requestMethod = MirroringMediaRequiredMethod(MirroringMediaSendRequest);
    SEL requestSelector = method_getName(requestMethod);
    IMP requestOriginal = method_getImplementation(requestMethod);
    IMP requestReplacement = imp_implementationWithBlock(^(id object, NSString *requestID, NSDictionary *request,
                                                          id destinationID, id options, id responseHandler) {
        if ([requestID isEqualToString:@"_streamStart"] &&
                ([request[@"_streamID"] isEqual:@"com.apple.MediaTransport.video"] ||
                 [request[@"_streamID"] isEqual:@"com.apple.MediaTransport.audio"]) &&
                [request[@"_streamType"] intValue] == 3) {
            NSMutableDictionary *forwarded = [request mutableCopy];
            forwarded[@"_streamType"] = @1;
            forwarded[@"_tf"] = @(0x10000);
            forwarded[@"_streamFlags"] = @4;
            request = forwarded;
            os_log(OS_LOG_DEFAULT, "MEDIA_WORKSPACE remote_udp_socket stream=%{public}s original_type=3 forwarded_type=1",
                   [request[@"_streamID"] UTF8String]);
        }
        ((void (*)(id, SEL, NSString *, NSDictionary *, id, id, id))requestOriginal)(
            object, requestSelector, requestID, request, destinationID, options, responseHandler);
    });

    Method tilesMethod = MirroringMediaRequiredMethod(MirroringMediaTiles);
    SEL tilesSelector = method_getName(tilesMethod);
    IMP tilesOriginal = method_getImplementation(tilesMethod);
    IMP tilesReplacement = imp_implementationWithBlock(^uint32_t(id object) {
        uint32_t native = ((uint32_t (*)(id, SEL))tilesOriginal)(object, tilesSelector);
        return native > 1 ? 1 : native;
    });

    Method trafficMethod = MirroringMediaRequiredMethod(MirroringMediaTrafficFlags);
    SEL trafficSelector = method_getName(trafficMethod);
    IMP trafficOriginal = method_getImplementation(trafficMethod);
    IMP trafficReplacement = imp_implementationWithBlock(^(id object, uint32_t flags) {
        // Optional traffic registration is unavailable on the reference host.
        uint32_t forwarded = flags == 0x10000 ? 0 : flags;
        ((void (*)(id, SEL, uint32_t))trafficOriginal)(object, trafficSelector, forwarded);
    });

    Method streamMethod = MirroringMediaRequiredMethod(MirroringMediaStreamFlags);
    SEL streamSelector = method_getName(streamMethod);
    IMP streamOriginal = method_getImplementation(streamMethod);
    IMP streamReplacement = imp_implementationWithBlock(^(id object, uint32_t flags) {
        // Reference MTK host has awdl0 without llw0. The remote _streamStart
        // above explicitly advertises Handoff (4); local flags use ordinary AWDL.
        uint32_t forwarded = flags == 0x6 ? 0 : flags;
        ((void (*)(id, SEL, uint32_t))streamOriginal)(object, streamSelector, forwarded);
    });
    if (!requestReplacement || !tilesReplacement || !trafficReplacement || !streamReplacement) {
        for (NSValue *value in @[[NSValue valueWithPointer:requestReplacement], [NSValue valueWithPointer:tilesReplacement],
                                [NSValue valueWithPointer:trafficReplacement], [NSValue valueWithPointer:streamReplacement]]) {
            if (value.pointerValue) imp_removeBlock(value.pointerValue);
        }
        return NO;
    }
    method_setImplementation(requestMethod, requestReplacement);
    method_setImplementation(tilesMethod, tilesReplacement);
    method_setImplementation(trafficMethod, trafficReplacement);
    method_setImplementation(streamMethod, streamReplacement);
    return YES;
}
