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

static const struct {
    const char *className;
    const char *selector;
    const char *encoding;
} launchContracts[] = {
    {"RBSLaunchRequest", "context", "@16@0:8"},
    {"RBSLaunchContext", "bundleIdentifier", "@16@0:8"},
    {"RBSLaunchContext", "executablePath", "@16@0:8"},
    {"RBSLaunchContext", "environment", "@16@0:8"},
    {"RBSLaunchContext", "setEnvironment:", "v24@0:8@16"},
    {"RBSLaunchRequest", "execute:assertion:error:", "c40@0:8o^@16o^@24o^@32"}
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

BOOL MirroringMediaHostSupported(NSString *architecture, NSString *version) {
    if (![architecture isEqual:@"x86_64"] || !version.length) return NO;
    NSArray<NSString *> *parts = [version componentsSeparatedByString:@"."];
    if (parts.count > 3) return NO;
    NSCharacterSet *invalid = [[NSCharacterSet characterSetWithCharactersInString:@"0123456789"] invertedSet];
    for (NSString *part in parts) {
        if (!part.length || [part rangeOfCharacterFromSet:invalid].location != NSNotFound) return NO;
    }
    return [parts.firstObject integerValue] >= 15;
}

NSDictionary *MirroringMediaCompatibilityReport(void) {
    NSDictionary *loaded = frameworks();
    NSString *architecture = system_value("hw.machine");
    NSString *build = system_value("kern.osversion");
    NSString *version = system_value("kern.osproductversion");
    // Keep the live-tested reference separate from experimental compatibility.
    // Symbol presence/type encodings cannot prove private implementation behavior.
    BOOL referenceMatch = [architecture isEqual:@"x86_64"] && [build isEqual:@"25G332"];
    BOOL hostSupported = MirroringMediaHostSupported(architecture, version);
    BOOL compatible = hostSupported;
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
        @"architecture": architecture, @"os_build": build, @"os_version": version,
        @"minimum_macos": @"15.0", @"host_supported": @(hostSupported),
        @"experimental_host": @(!referenceMatch),
        @"reference_build": @"25G332", @"reference_match": @(referenceMatch), @"frameworks": loaded,
        @"methods": methods, @"private_symbols": symbols,
        @"native_mirroring_verified": [NSNull null],
        @"scope": @"Host ABI checks only. Device compatibility, connection, video, audio and input require live validation."};
}

// Extra startup checks run in the isolated inspector, not inside the media app.
NSDictionary *MirroringHostCompatibilityReport(void) {
    NSMutableDictionary *report = [MirroringMediaCompatibilityReport() mutableCopy];
    BOOL compatible = [report[@"compatible"] boolValue];
    NSMutableDictionary *loaded = [report[@"frameworks"] mutableCopy];
    NSMutableDictionary *symbols = [report[@"private_symbols"] mutableCopy];
    NSDictionary *imports = @{
        @"RunningBoardServices": @[],
        @"MobileActivationMacOS": @[@"MAECopyDCRTMacOSWithError", @"MAEIssueDCRTMacOSWithCallback"],
        @"AppleKeyStore": @[@"aks_create_signing_key_with_params", @"aks_validate_local_key",
            @"aks_sign_signing_key", @"aks_remote_session", @"aks_remote_session_step",
            @"aks_remote_session_token", @"aks_remote_peer_confirm", @"aks_remote_peer_setup",
            @"aks_remote_session_reset", @"aks_remote_session_reset_all",
            @"aks_remote_peer_get_state", @"aks_remote_peer_drop"]};
    for (NSString *name in imports) {
        NSString *path = [NSString stringWithFormat:@"/System/Library/PrivateFrameworks/%@.framework", name];
        void *handle = dlopen([[path stringByAppendingPathComponent:name] fileSystemRepresentation], RTLD_NOW | RTLD_LOCAL);
        NSBundle *bundle = [NSBundle bundleWithPath:path];
        loaded[name] = @{@"loaded": @(handle != NULL),
            @"version": [bundle objectForInfoDictionaryKey:@"CFBundleVersion"] ?: @"unavailable"};
        compatible &= handle != NULL;
        for (NSString *symbol in imports[name]) {
            BOOL found = handle && dlsym(handle, symbol.UTF8String) != NULL;
            symbols[symbol] = @(found);
            compatible &= found;
        }
    }
    NSMutableArray *launchMethods = [NSMutableArray array];
    for (NSUInteger index = 0; index < sizeof(launchContracts) / sizeof(launchContracts[0]); index++) {
        Method method = class_getInstanceMethod(objc_getClass(launchContracts[index].className),
                                               sel_registerName(launchContracts[index].selector));
        const char *actual = method ? method_getTypeEncoding(method) : NULL;
        BOOL matches = actual && strcmp(actual, launchContracts[index].encoding) == 0;
        compatible &= matches;
        [launchMethods addObject:@{@"class": @(launchContracts[index].className),
            @"selector": @(launchContracts[index].selector), @"matches": @(matches),
            @"expected_encoding": @(launchContracts[index].encoding),
            @"actual_encoding": actual ? @(actual) : (id)[NSNull null]}];
    }
    report[@"frameworks"] = loaded;
    report[@"private_symbols"] = symbols;
    report[@"launch_methods"] = launchMethods;
    report[@"compatible"] = @(compatible);
    return report;
}
