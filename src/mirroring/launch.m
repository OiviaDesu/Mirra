// App-specific launch environment at the native LaunchServices entry point.
// Experimental: the controller must validate this host and restore its agent.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <libproc.h>
#include <sys/stat.h>
#include <unistd.h>

static NSString *settingsPath;
static Class contextClass;
static id getObject(id object, const char *name) {
    return ((id (*)(id, SEL))objc_msgSend)(object, sel_registerName(name));
}
static bool signature(Class cls, const char *selector, const char *encoding) {
    Method method = class_getInstanceMethod(cls, sel_registerName(selector));
    return method && !strcmp(method_getTypeEncoding(method), encoding);
}

static void prepareContext(id request) {
    id context = getObject(request, "context");
    if (![context isKindOfClass:contextClass]) return;
    if (![getObject(context, "bundleIdentifier") isEqual:@"com.apple.ScreenContinuity"] ||
        ![getObject(context, "executablePath") isEqual:@"/System/Applications/iPhone Mirroring.app/Contents/MacOS/iPhone Mirroring"]) return;
    // Re-read an owned, small configuration for each launch so a future
    // supervisor can publish a fresh session without restarting this agent.
    struct stat info;
    if (lstat(settingsPath.fileSystemRepresentation, &info) || !S_ISREG(info.st_mode) ||
        info.st_uid != geteuid() || (info.st_mode & 0077) || info.st_size > 16384) return;
    NSData *data = [NSData dataWithContentsOfFile:settingsPath];
    if (!data || data.length > 16384) return;
    id settings = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if (![settings isKindOfClass:NSDictionary.class] || ![settings count]) return;
    NSSet *allowed = [NSSet setWithArray:@[@"DYLD_INSERT_LIBRARIES", @"MIRRA_BIND_SOCKET", @"MIRRA_LAUNCH_PROBE"]];
    for (id key in settings) {
        id value = settings[key];
        if (![allowed containsObject:key] || ![value isKindOfClass:NSString.class] ||
            ![value length] || [value rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound) return;
    }
    id existing = getObject(context, "environment");
    if (existing && ![existing isKindOfClass:NSDictionary.class]) return;
    // Respect an existing loader configuration rather than replacing it.
    if (existing[@"DYLD_INSERT_LIBRARIES"] &&
        ![existing[@"DYLD_INSERT_LIBRARIES"] isEqual:settings[@"DYLD_INSERT_LIBRARIES"]]) {
        fprintf(stderr, "MIRRA_LAUNCH existing_loader_conflict\n");
        return;
    }
    NSMutableDictionary *environment = existing ? [existing mutableCopy] : [NSMutableDictionary dictionary];
    [environment addEntriesFromDictionary:settings];
    ((void (*)(id, SEL, id))objc_msgSend)(context, sel_registerName("setEnvironment:"), environment);
    fprintf(stderr, "MIRRA_LAUNCH target_environment_applied\n");
}

__attribute__((constructor)) static void loaded(void) {
    @autoreleasepool {
        char process[PROC_PIDPATHINFO_MAXSIZE] = {0};
        if (proc_pidpath(getpid(), process, sizeof(process)) <= 0 ||
            strcmp(process, "/System/Library/CoreServices/CoreServicesUIAgent.app/Contents/MacOS/CoreServicesUIAgent")) return;
        const char *path = getenv("MIRRA_LAUNCH_SETTINGS");
        if (!path || path[0] != '/') return;
        settingsPath = @(path);
        if (!dlopen("/System/Library/PrivateFrameworks/RunningBoardServices.framework/RunningBoardServices", RTLD_LAZY)) return;
        Class requestClass = NSClassFromString(@"RBSLaunchRequest");
        contextClass = NSClassFromString(@"RBSLaunchContext");
        if (!signature(requestClass, "context", "@16@0:8") ||
            !signature(contextClass, "bundleIdentifier", "@16@0:8") ||
            !signature(contextClass, "executablePath", "@16@0:8") ||
            !signature(contextClass, "environment", "@16@0:8") ||
            !signature(contextClass, "setEnvironment:", "v24@0:8@16") ||
            !signature(requestClass, "execute:assertion:error:", "c40@0:8o^@16o^@24o^@32")) {
            fprintf(stderr, "MIRRA_LAUNCH unsupported_method_signatures\n"); return;
        }
        SEL selector = sel_registerName("execute:assertion:error:");
        Method method = class_getInstanceMethod(requestClass, selector);
        IMP original = method_getImplementation(method);
        IMP replacement = imp_implementationWithBlock(^BOOL(id object, void *process, void *assertion, void *error) {
            prepareContext(object);
            return ((BOOL (*)(id, SEL, void *, void *, void *))original)(object, selector, process, assertion, error);
        });
        method_setImplementation(method, replacement);
        fprintf(stderr, "MIRRA_LAUNCH_ADAPTER_READY pid=%d\n", getpid());
    }
}
