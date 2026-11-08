#import "profile.h"
#include <dlfcn.h>
#include <sys/sysctl.h>

static const struct {
    const char *className;
    const char *selector;
    const char *encoding;
} contracts[MirroringMediaMethodCount] = {
    {"RPRemoteDisplaySession", "sendRequestID:request:destinationID:options:responseHandler:", "v56@0:8@16@24@32@40@?48"},
    {"VCMediaNegotiatorLocalConfiguration", "tilesPerVideoFrame", "I16@0:8"},
    {"RPStreamSession", "setTrafficFlags:", "v20@0:8I16"},
    {"RPStreamSession", "setStreamFlags:", "v20@0:8I16"}
};

static NSString *system_value(const char *name) {
    char value[256] = {0};
    size_t length = sizeof(value);
    if (sysctlbyname(name, value, &length, NULL, 0) != 0) return @"unavailable";
    return [NSString stringWithUTF8String:value] ?: @"unavailable";
}

static NSDictionary *frameworks(void) {
    static NSDictionary *result;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSMutableDictionary *loaded = [NSMutableDictionary dictionary];
        for (NSString *name in @[@"Rapport", @"AVConference", @"Network"]) {
            NSString *root = [name isEqual:@"Network"] ? @"Frameworks" : @"PrivateFrameworks";
            NSString *path = [NSString stringWithFormat:@"/System/Library/%@/%@.framework", root, name];
            void *handle = dlopen([[path stringByAppendingPathComponent:name] fileSystemRepresentation], RTLD_NOW | RTLD_LOCAL);
            NSBundle *bundle = [NSBundle bundleWithPath:path];
            loaded[name] = @{@"loaded": @(handle != NULL),
                @"version": [bundle objectForInfoDictionaryKey:@"CFBundleVersion"] ?: @"unavailable"};
        }
        result = [loaded copy];
    });
    return result;
}

Method MirroringMediaRequiredMethod(MirroringMediaMethod entry) {
    if (entry >= MirroringMediaMethodCount) return NULL;
    frameworks();
    return class_getInstanceMethod(objc_getClass(contracts[entry].className),
                                   sel_registerName(contracts[entry].selector));
}

NSDictionary *MirroringMediaCompatibilityReport(void) {
    NSDictionary *loaded = frameworks();
    NSString *architecture = system_value("hw.machine");
    NSString *build = system_value("kern.osversion");
    // Objective-C type encodings do not describe private C functions. Their
    // two-argument/ownership contract was inspected on this exact OS build.
    BOOL referenceMatch = [architecture isEqual:@"x86_64"] && [build isEqual:@"25G332"];
    BOOL compatible = referenceMatch;
    NSMutableArray *methods = [NSMutableArray array];
    for (NSUInteger index = 0; index < MirroringMediaMethodCount; index++) {
        Method method = MirroringMediaRequiredMethod(index);
        const char *actual = method ? method_getTypeEncoding(method) : NULL;
        BOOL matches = actual && strcmp(actual, contracts[index].encoding) == 0;
        compatible &= matches;
        [methods addObject:@{@"class": @(contracts[index].className), @"selector": @(contracts[index].selector),
            @"expected_encoding": @(contracts[index].encoding), @"actual_encoding": actual ? @(actual) : (id)[NSNull null],
            @"matches": @(matches)}];
    }
    NSMutableDictionary *symbols = [NSMutableDictionary dictionary];
    for (NSString *symbol in @[@"nw_interface_create_with_index", @"nw_path_create_evaluator_for_endpoint"]) {
        BOOL found = dlsym(RTLD_DEFAULT, symbol.UTF8String) != NULL;
        symbols[symbol] = @(found);
        compatible &= found;
    }
    for (NSDictionary *framework in loaded.allValues) compatible &= [framework[@"loaded"] boolValue];
    return @{@"schema_version": @1, @"profile": @MIRRA_MEDIA_PROFILE, @"compatible": @(compatible),
        @"architecture": architecture, @"os_build": build, @"os_version": system_value("kern.osproductversion"),
        @"reference_build": @"25G332", @"reference_match": @(referenceMatch), @"frameworks": loaded,
        @"methods": methods, @"private_symbols": symbols,
        @"native_mirroring_verified": [NSNull null],
        @"scope": @"Host ABI checks only. Device compatibility, connection, video, audio and input require live validation."};
}
