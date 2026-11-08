// Inspect local framework metadata and the read-only regional eligibility gate.
// Reports metadata, catalog counts and selected built-in Apple app records.
// Never reads notification contents or enumerates personal application names.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <stdbool.h>
#include <sys/sysctl.h>

typedef bool (*EligibilityGetter)(void) __attribute__((swiftcall));

static void notificationGates(NSMutableDictionary *report) {
    char build[128] = {0};
    size_t size = sizeof(build);
    if (sysctlbyname("kern.osversion", build, &size, NULL, 0) == 0)
        report[@"os_build"] = @(build);
    // C exports have no runtime type encoding. Invoke only on the host ABI
    // inspected in this project; other builds can still use ObjC getters.
#if defined(__x86_64__)
    bool inspected = !strcmp(build, "25G332");
#else
    bool inspected = false;
#endif
    report[@"notification_gate_abi_supported"] = @(inspected);
    Boolean present = false;
    Boolean allowed = CFPreferencesGetAppBooleanValue(CFSTR("allowiPhoneMirroring"),
        CFSTR("com.apple.applicationaccess"), &present);
    report[@"mirroring_policy_explicit"] = @((bool)present);
    // The inspected notification implementation permits an absent preference.
    if (present) report[@"mirroring_policy_allowed"] = @((bool)allowed);
    if (!inspected) return;
    void *framework = dlopen("/System/Library/Frameworks/UserNotifications.framework/UserNotifications",
                             RTLD_LAZY | RTLD_LOCAL);
    NSMutableDictionary *flags = [NSMutableDictionary dictionary];
    for (NSString *name in @[@"UNCOneness", @"UNCDaemonEnabled", @"UNCRemoteServicesNeeded"]) {
        bool (*getter)(void) = framework ? dlsym(framework, name.UTF8String) : NULL;
        if (getter) flags[name] = @(getter());
    }
    report[@"notification_feature_flags"] = flags;
    void *eligibility = dlopen("/usr/lib/system/libsystem_eligibility.dylib", RTLD_LAZY | RTLD_LOCAL);
    int (*answerGetter)(uint64_t, uint64_t *, void *, void *, void *) = eligibility ?
        dlsym(eligibility, "os_eligibility_get_domain_answer") : NULL;
    if (answerGetter) {
        uint64_t answer = 0;
        // Domain 27 is the domain read by RemoteNotificationsProperties on
        // 25G332. Preserve the answer instead of inventing enum names.
        int status = answerGetter(27, &answer, NULL, NULL, NULL);
        report[@"notification_eligibility"] = @{@"domain":@27, @"status":@(status),
            @"answer_raw":@(answer), @"gate_passes":@((bool)(status == 0 && (answer == 1 || answer == 4)))};
    }
}

static NSArray *methods(Class cls) {
    NSMutableArray *result = [NSMutableArray array];
    unsigned count = 0;
    Method *list = class_copyMethodList(cls, &count);
    for (unsigned i = 0; i < count; i++) {
        NSString *name = NSStringFromSelector(method_getName(list[i]));
        if ([name hasPrefix:@"."]) continue;
        [result addObject:@{@"selector":name, @"encoding":@(method_getTypeEncoding(list[i]))}];
    }
    free(list);
    return [result sortedArrayUsingDescriptors:@[[NSSortDescriptor sortDescriptorWithKey:@"selector" ascending:YES]]];
}

int main(int argc, char **argv) {
    bool readSettings = argc == 2 && strcmp(argv[1], "--settings") == 0;
    bool launchMetadata = argc == 2 && strcmp(argv[1], "--launch-metadata") == 0;
    bool remoteAppCount = argc == 2 && strcmp(argv[1], "--remote-app-count") == 0;
    if (argc > 1 && !readSettings && !launchMetadata && !remoteAppCount) return 64;
    @autoreleasepool {
        NSMutableDictionary *report = [NSMutableDictionary dictionaryWithObject:@1 forKey:@"schema_version"];
        if (remoteAppCount) {
            dlopen("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/LaunchServices", RTLD_LAZY | RTLD_LOCAL);
            Class cls = NSClassFromString(@"LSApplicationRecord");
            SEL selector = sel_registerName("remotePlaceholderEnumerator");
            Method method = class_getClassMethod(cls, selector);
            report[@"remote_catalog_verified"] = @NO;
            if (method && !strcmp(method_getTypeEncoding(method), "@16@0:8")) {
                id enumerator = ((id (*)(id, SEL))objc_msgSend)(cls, selector);
                report[@"enumerator_class"] = enumerator ? NSStringFromClass([enumerator class]) : @"unavailable";
                if ([enumerator isKindOfClass:NSEnumerator.class]) {
                    NSUInteger count = 0;
                    id item = nil;
                    NSMutableArray *knownApps = [NSMutableArray array];
                    NSMutableArray *knownRecords = [NSMutableArray array];
                    NSSet *builtins = [NSSet setWithArray:@[@"com.apple.Preferences", @"com.apple.mobilecal", @"com.apple.mobiletimer"]];
                    while (count < 10000 && (item = [enumerator nextObject])) {
                        count++;
                        if (count == 1) {
                            report[@"record_class"] = NSStringFromClass([item class]);
                            report[@"record_methods"] = methods([item class]);
                        }
                        SEL bundleSelector = sel_registerName("bundleIdentifier");
                        Method bundleGetter = class_getInstanceMethod([item class], bundleSelector);
                        if (bundleGetter && !strcmp(method_getTypeEncoding(bundleGetter), "@16@0:8")) {
                            id bundle = ((id (*)(id, SEL))objc_msgSend)(item, bundleSelector);
                            if ([builtins containsObject:bundle]) {
                                [knownApps addObject:bundle];
                                SEL urlSelector = sel_registerName("URL");
                                Method urlGetter = class_getInstanceMethod([item class], urlSelector);
                                if (urlGetter && !strcmp(method_getTypeEncoding(urlGetter), "@16@0:8")) {
                                    id url = ((id (*)(id, SEL))objc_msgSend)(item, urlSelector);
                                    if ([url isKindOfClass:NSURL.class])
                                        [knownRecords addObject:@{@"bundle_id":bundle, @"url":[url absoluteString]}];
                                }
                            }
                        }
                    }
                    report[@"remote_placeholder_count"] = @(count);
                    report[@"count_capped"] = @((bool)(count == 10000));
                    report[@"known_apple_apps"] = knownApps;
                    report[@"known_apple_records"] = knownRecords;
                }
            }
            NSData *data = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:NULL];
            fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout);
            return 0;
        }
        if (launchMetadata) {
            dlopen("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/LaunchServices", RTLD_LAZY | RTLD_LOCAL);
            dlopen("/System/Library/PrivateFrameworks/RunningBoardServices.framework/RunningBoardServices", RTLD_LAZY | RTLD_LOCAL);
            dlopen("/System/Library/Frameworks/AppKit.framework/AppKit", RTLD_LAZY | RTLD_LOCAL);
            for (NSString *name in @[@"RBSLaunchRequest", @"RBSLaunchContext", @"RBSProcessIdentity", @"_LSDOpenClient", @"_LSOpenConfiguration", @"LSApplicationRecord", @"NSWorkspace", @"NSWorkspaceOpenConfiguration"]) {
                Class cls = NSClassFromString(name);
                report[name] = @{@"available":@(cls != Nil), @"instance_methods":methods(cls), @"class_methods":methods(object_getClass(cls))};
            }
            NSData *data = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:NULL];
            fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout);
            return 0;
        }
        void *continuity = dlopen("/System/Library/PrivateFrameworks/ScreenContinuityServices.framework/ScreenContinuityServices", RTLD_LAZY | RTLD_LOCAL);
        EligibilityGetter eligible = continuity ? (EligibilityGetter)dlsym(continuity,
            "$s24ScreenContinuityServices0B11EligibilityV25isiPhoneMirroringEligibleSbvgZ") : NULL;
        report[@"eligibility_getter_available"] = @(eligible != NULL);
        if (eligible) report[@"continuity_eligible"] = @(eligible());
        report[@"notifications_framework_loaded"] = @(dlopen("/System/Library/PrivateFrameworks/UserNotificationsSettings.framework/UserNotificationsSettings", RTLD_LAZY | RTLD_LOCAL) != NULL);
        NSMutableDictionary *classes = [NSMutableDictionary dictionary];
        for (NSString *name in @[@"UNNotificationSettingsCenter", @"UNNotificationSystemSettings", @"UNUserNotificationSettingsServiceConnection"]) {
            Class cls = NSClassFromString(name);
            classes[name] = @{@"available":@(cls != Nil), @"instance_methods":methods(cls), @"class_methods":methods(object_getClass(cls))};
        }
        report[@"classes"] = classes;
        if (readSettings) {
            notificationGates(report);
            // Exact signatures are checked before calling the private getters.
            // Keep the raw enum: a settings value alone does not prove delivery.
            Class cls = NSClassFromString(@"UNNotificationSettingsCenter");
            SEL current = sel_registerName("currentNotificationSettingsCenter");
            SEL system = sel_registerName("notificationSystemSettings");
            SEL remote = sel_registerName("remoteNotificationsSetting");
            Method factory = class_getClassMethod(cls, current);
            Method getter = class_getInstanceMethod(cls, system);
            Method setting = class_getInstanceMethod(NSClassFromString(@"UNNotificationSystemSettings"), remote);
            if (factory && getter && setting && !strcmp(method_getTypeEncoding(factory), "@16@0:8") &&
                !strcmp(method_getTypeEncoding(getter), "@16@0:8") && !strcmp(method_getTypeEncoding(setting), "q16@0:8")) {
                report[@"notification_settings_signature_supported"] = @YES;
                id center = ((id (*)(id, SEL))objc_msgSend)(cls, current);
                id settings = center ? ((id (*)(id, SEL))objc_msgSend)(center, system) : nil;
                report[@"notification_settings_returned"] = @((bool)(settings != nil));
                if ([settings isKindOfClass:NSClassFromString(@"UNNotificationSystemSettings")]) {
                    report[@"remote_notifications_setting_raw"] = @(((NSInteger (*)(id, SEL))objc_msgSend)(settings, remote));
                    // Read Apple's own rendering of this field rather than
                    // assuming its private enum matches the public SDK enum.
                    NSString *description = [settings description];
                    NSRegularExpression *pattern = [NSRegularExpression regularExpressionWithPattern:
                        @"remoteNotificationsSetting:\\s*(NotSupported|Disabled|Enabled)(?=[,;> ])"
                        options:0 error:NULL];
                    NSTextCheckingResult *match = [pattern firstMatchInString:description options:0
                        range:NSMakeRange(0, description.length)];
                    if (match) report[@"remote_notifications_setting_label"] =
                        [description substringWithRange:[match rangeAtIndex:1]];
                }
            } else report[@"notification_settings_signature_supported"] = @NO;
        }
        report[@"native_notifications_verified"] = @NO;
        report[@"native_app_launch_verified"] = @NO;
        NSData *json = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:NULL];
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
    }
    return 0;
}
