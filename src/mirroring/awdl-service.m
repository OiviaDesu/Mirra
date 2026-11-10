// Adapted from @OiviaDesu's PR #4, commit 2b55ad4. See docs/Media.md.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "awdl-service.h"

// An Objective-C init consumes self and returns a retained object. Preserve
// that convention across the C function pointer and the replacement IMP.
typedef id (*ReservationInit)(id __attribute__((ns_consumed)), SEL,
                             __unsafe_unretained id) __attribute__((ns_returns_retained));
static ReservationInit original_init;
static uintptr_t invalid_service;
static Class patched_class;
static pthread_mutex_t install_lock = PTHREAD_MUTEX_INITIALIZER;

static id reservation_init(id self __attribute__((ns_consumed)), SEL command,
                           __unsafe_unretained id service) __attribute__((ns_returns_retained));
static id reservation_init(id self __attribute__((ns_consumed)), SEL command,
                           __unsafe_unretained id service) {
    // The wrapper's instruction bytes have been read by Apple code as an
    // NSString pointer. Do not retain or message that value before replacing it.
    if ((uintptr_t)(__bridge const void *)service == invalid_service)
        service = @"RemoteScreen";
    return original_init(self, command, service);
}

static bool known_stub(const void *symbol) {
#if defined(__x86_64__)
    static const unsigned char stub[] = {0x55,0x48,0x89,0xe5,0x31,0xc0,0x5d,0xc3};
    Dl_info image;
    if (!symbol || !dladdr(symbol, &image) || !image.dli_fbase) return false;
    const struct mach_header_64 *header = image.dli_fbase;
    if (header->magic != MH_MAGIC_64 || header->cputype != CPU_TYPE_X86_64) return false;
    unsigned long size = 0;
    const unsigned char *text = getsectiondata(header, "__TEXT", "__text", &size);
    uintptr_t address = (uintptr_t)symbol, start = (uintptr_t)text;
    if (!text || size < sizeof(stub) || address < start || address - start > size - sizeof(stub)) return false;
    return !memcmp(symbol, stub, sizeof(stub));
#else
    (void)symbol;
    return false;
#endif
}

static bool object_initializer(Method method) {
    if (!method || method_getNumberOfArguments(method) != 3) return false;
    char *result = method_copyReturnType(method);
    bool matches = result && !strcmp(result, "@");
    free(result);
    const char *expected[] = {"@", ":", "@"};
    for (unsigned i = 0; i < 3; i++) {
        char *argument = method_copyArgumentType(method, i);
        matches = matches && argument && !strcmp(argument, expected[i]);
        free(argument);
    }
    return matches;
}

static bool install_repair(Class target, const void *symbol) {
    if (!target || class_isMetaClass(target) || !known_stub(symbol)) return false;
    SEL selector = sel_registerName("initForService:");
    Method method = class_getInstanceMethod(target, selector);
    if (!object_initializer(method)) return false;
    pthread_mutex_lock(&install_lock);
    if (patched_class) {
        bool installed = patched_class == target &&
            method_getImplementation(method) == (IMP)reservation_init;
        pthread_mutex_unlock(&install_lock);
        return installed;
    }
    memcpy(&invalid_service, symbol, sizeof(invalid_service));
    original_init = (ReservationInit)method_getImplementation(method);
    // class_replaceMethod adds an override for inherited methods, avoiding any
    // change to the superclass or unrelated reservations in the process.
    class_replaceMethod(target, selector, (IMP)reservation_init, method_getTypeEncoding(method));
    patched_class = target;
    pthread_mutex_unlock(&install_lock);
    return true;
}

bool MirraRepairAWDLService(void) {
    return install_repair(objc_getClass("WiFiP2PTrafficRegistrationReservation"),
        dlsym(RTLD_DEFAULT, "_AWDLTrafficRegistrationServiceRemoteScreen"));
}
