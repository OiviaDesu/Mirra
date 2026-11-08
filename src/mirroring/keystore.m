#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <arpa/inet.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

// The public SDK no longer ships dyld-interposing.h. dyld still consumes
// replacement/original pointer pairs in the Mach-O __interpose section.
#ifndef DYLD_INTERPOSE
#define DYLD_INTERPOSE(REPLACEMENT, ORIGINAL) \
    __attribute__((used)) static const struct { const void *new_function; const void *old_function; } \
    interpose_##ORIGINAL __attribute__((section("__DATA,__interpose"))) = \
    {(const void *)(REPLACEMENT), (const void *)(ORIGINAL)};
#endif

// Experimental runtime interposer. No on-disk system binary is patched.
// Genuine AKS operations run on the configured jailbroken backend.
extern NSData *MAECopyDCRTMacOSWithError(NSError *__autoreleasing *);
extern void MAEIssueDCRTMacOSWithCallback(NSDictionary *, void *, void (^)(NSError *));
extern int aks_create_signing_key_with_params(int, int, const void *, size_t, void **, size_t *);
extern int aks_validate_local_key(int, int, const void *, size_t);
extern int aks_sign_signing_key(int, int, const void *, size_t, int, const void *, size_t, void **, size_t *);
extern int aks_remote_session(int, unsigned int, const void *, size_t, const void *, size_t, const void *, size_t, int *);
extern int aks_remote_session_step(int, const void *, size_t, void **, size_t *);
extern int aks_remote_session_token(int, void **, size_t *);
extern int aks_remote_peer_confirm(int, void **, size_t *);
extern int aks_remote_peer_setup(int, const void *, size_t, void **, size_t *);
extern int aks_remote_session_reset(int);
extern int aks_remote_session_reset_all(void);
extern int aks_remote_peer_get_state(int, const void *, size_t, void *);
extern int aks_remote_peer_drop(int, const void *, size_t);

static NSLock *keys_lock;
static NSMutableSet<NSString *> *local_keys;
static NSMutableSet<NSString *> *remote_keys;
static NSString *broker_path;
static NSString *broker_token;
static unsigned short broker_port;
static NSString *dcrt_path;
static const int unavailable = (int)0xe00002bc;
extern void *_swift_stdlib_bridgeErrorToNSError(void *) __attribute__((swiftcall));

static void diagnostic_error(NSString *domain, NSInteger code, NSDictionary *info) {
    if ([domain isEqualToString:@"com.apple.sharing.authentication"]) {
        id description = info[NSDebugDescriptionErrorKey] ?: info[NSLocalizedDescriptionKey];
        if ([description isKindOfClass:NSString.class]) {
            NSString *safe = [(NSString *)description stringByReplacingOccurrencesOfString:@"[0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27,}|[A-Za-z0-9+/]{48,}={0,2}"
                                       withString:@"<redacted>" options:NSRegularExpressionSearch range:NSMakeRange(0, [(NSString *)description length])];
            if (safe.length > 500) safe = [safe substringToIndex:500];
            fprintf(stderr, "MIRRA_KEYSTORE native_auth_error code=%ld reason=%s\n", (long)code, safe.UTF8String);
        }
    }
}

static void *diagnostic_swift_error(void *) __attribute__((swiftcall));
static void *diagnostic_swift_error(void *error) {
    void *result = _swift_stdlib_bridgeErrorToNSError(error);
    static __thread BOOL logging;
    if (broker_path && result && !logging) {
        logging = YES;
        NSError *native = (__bridge NSError *)result;
        diagnostic_error(native.domain, native.code, native.userInfo);
        logging = NO;
    }
    return result;
}

static void initialize(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        keys_lock = [NSLock new];
        local_keys = [NSMutableSet set];
        remote_keys = [NSMutableSet set];
        const char *socket_path = getenv("MIRRA_KEYSTORE_BROKER_SOCKET");
        const char *certificate_path = getenv("MIRRA_KEYSTORE_DCRT_PATH");
        const char *tcp_port = getenv("MIRRA_KEYSTORE_BROKER_TCP_PORT");
        const char *token = getenv("MIRRA_KEYSTORE_BROKER_TOKEN");
        if (socket_path) broker_path = [NSString stringWithUTF8String:socket_path];
        if (certificate_path) dcrt_path = [NSString stringWithUTF8String:certificate_path];
        if (tcp_port && token) {
            long port = strtol(tcp_port, NULL, 10);
            if (port > 0 && port <= 65535) broker_port = (unsigned short)port;
            broker_token = [NSString stringWithUTF8String:token];
        }
    });
}

static NSString *encoded(const void *data, size_t size) {
    if (!data || !size || size > 65536) return @"";
    return [[NSData dataWithBytes:data length:size] base64EncodedStringWithOptions:0];
}

static BOOL owns(NSMutableSet *keys, const void *data, size_t size) {
    initialize();
    if (!broker_path || size > 65536) return NO;
    NSString *value = encoded(data, size);
    [keys_lock lock];
    BOOL present = [keys containsObject:value];
    [keys_lock unlock];
    return present;
}

static BOOL transfer(int fd, void *buffer, size_t count, BOOL writing) {
    uint8_t *position = buffer;
    while (count) {
        ssize_t size = writing ? send(fd, position, count, 0) : recv(fd, position, count, 0);
        if (size <= 0) return NO;
        position += size;
        count -= (size_t)size;
    }
    return YES;
}

static NSDictionary *rpc(NSDictionary *request) {
    initialize();
    if (!broker_path || broker_path.length >= sizeof(((struct sockaddr_un *)0)->sun_path)) return nil;
    BOOL tcp = broker_port && broker_token.length;
    int fd = socket(tcp ? AF_INET : AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return nil;
    int on = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, sizeof(on));
    struct timeval timeout = {15, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    int connected;
    if (tcp) {
        struct sockaddr_in address = {0};
        address.sin_len = sizeof(address);
        address.sin_family = AF_INET;
        address.sin_port = htons(broker_port);
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        connected = connect(fd, (struct sockaddr *)&address, sizeof(address));
    } else {
        struct sockaddr_un address = {0};
        address.sun_len = sizeof(address);
        address.sun_family = AF_UNIX;
        strlcpy(address.sun_path, broker_path.fileSystemRepresentation, sizeof(address.sun_path));
        connected = connect(fd, (struct sockaddr *)&address, sizeof(address));
    }
    if (connected != 0) {
        fprintf(stderr, "MIRRA_KEYSTORE transport_connect_error=%d tcp=%d\n", errno, tcp);
        close(fd);
        return nil;
    }
    NSMutableDictionary *wire_request = [request mutableCopy];
    if (tcp) wire_request[@"_auth"] = broker_token;
    NSData *body = [NSJSONSerialization dataWithJSONObject:wire_request options:0 error:NULL];
    uint32_t size = htonl((uint32_t)body.length);
    BOOL success = body.length <= 262144 && transfer(fd, &size, sizeof(size), YES) &&
                   transfer(fd, (void *)body.bytes, body.length, YES) && transfer(fd, &size, sizeof(size), NO);
    size = ntohl(size);
    if (!success || size > 262144) {
        fprintf(stderr, "MIRRA_KEYSTORE transport_frame_error=%d tcp=%d\n", errno, tcp);
        close(fd);
        return nil;
    }
    NSMutableData *reply = [NSMutableData dataWithLength:size];
    success = transfer(fd, reply.mutableBytes, size, NO);
    close(fd);
    NSDictionary *value = success ? [NSJSONSerialization JSONObjectWithData:reply options:0 error:NULL] : nil;
    if (![value isKindOfClass:NSDictionary.class]) value = nil;
    fprintf(stderr, "MIRRA_KEYSTORE operation=%s status=%d bytes=%lu\n", [request[@"operation"] UTF8String],
            value[@"status"] ? [value[@"status"] intValue] : unavailable, (unsigned long)[value[@"bytes"] unsignedIntegerValue]);
    return value;
}

static int status(NSDictionary *response) {
    return response[@"status"] ? [response[@"status"] intValue] : unavailable;
}

static BOOL owns_local(const void *data, size_t size) {
    initialize();
    if (owns(local_keys, data, size)) return YES;
    if (!broker_path || size < 512 || size > 65536) return NO;
    NSDictionary *response = rpc(@{@"operation": @"validate", @"local_ltk": encoded(data, size)});
    if (status(response) != 0) return NO;
    [keys_lock lock];
    [local_keys addObject:encoded(data, size)];
    [keys_lock unlock];
    return YES;
}

static int copy_output(NSDictionary *response, void **output, size_t *size) {
    if (!output || !size) return (int)0xe00002c2;
    *output = NULL;
    *size = 0;
    NSString *value = response[@"data"];
    NSData *data = [value isKindOfClass:NSString.class] ? [[NSData alloc] initWithBase64EncodedString:value options:0] : nil;
    if (data.length && data.length <= 65536) {
        void *bytes = malloc(data.length);
        if (!bytes) return (int)0xe00002bd;
        memcpy(bytes, data.bytes, data.length);
        *output = bytes;
        *size = data.length;
    }
    return status(response);
}

static BOOL virtual_session(int session) {
    return (unsigned int)session > 0x6F000000 && (unsigned int)session < 0x70000000;
}

static NSData *mirra_copy_dcrt(NSError *__autoreleasing *error) {
    initialize();
    if (!broker_path) return MAECopyDCRTMacOSWithError(error);
    NSData *certificate = [NSData dataWithContentsOfFile:dcrt_path];
    if (!certificate.length && error)
        *error = [NSError errorWithDomain:@"com.apple.MobileActivation.ErrorDomain" code:-1 userInfo:nil];
    fprintf(stderr, "MIRRA_KEYSTORE copyDCRT bytes=%lu\n", (unsigned long)certificate.length);
    return certificate.length ? certificate : nil;
}

static void mirra_issue_dcrt(NSDictionary *options, void *context, void (^completion)(NSError *)) {
    initialize();
    if (!broker_path) {
        MAEIssueDCRTMacOSWithCallback(options, context, completion);
        return;
    }
    NSDictionary *health = rpc(@{@"operation": @"health"});
    BOOL available = [health[@"ready"] boolValue] && [NSData dataWithContentsOfFile:dcrt_path].length > 0;
    NSError *error = available ? nil : [NSError errorWithDomain:@"com.apple.MobileActivation.ErrorDomain" code:-1 userInfo:nil];
    if (completion) dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{ completion(error); });
}

static int mirra_create(int bag, int key_class, const void *parameters, size_t parameters_size, void **output, size_t *size) {
    initialize();
    if (!broker_path || !parameters || !parameters_size || (key_class != 9 && key_class != 10))
        return aks_create_signing_key_with_params(bag, key_class, parameters, parameters_size, output, size);
    NSDictionary *response = rpc(@{@"operation": @"generate", @"key_class": @(key_class)});
    int result = copy_output(response, output, size);
    if (result == 0 && *output && *size) {
        [keys_lock lock];
        [local_keys addObject:encoded(*output, *size)];
        [keys_lock unlock];
    }
    return result;
}

static int mirra_validate(int bag, int key_class, const void *key, size_t size) {
    if (!owns_local(key, size)) return aks_validate_local_key(bag, key_class, key, size);
    return status(rpc(@{@"operation": @"validate", @"local_ltk": encoded(key, size)}));
}

static int mirra_sign(int bag, int local_class, const void *local, size_t local_size, int remote_class,
                     const void *remote, size_t remote_size, void **output, size_t *size) {
    if (!owns_local(local, local_size))
        return aks_sign_signing_key(bag, local_class, local, local_size, remote_class, remote, remote_size, output, size);
    NSDictionary *response = rpc(@{@"operation": @"sign_remote", @"local_ltk": encoded(local, local_size),
                                   @"remote_ltk": encoded(remote, remote_size), @"remote_key_class": @(remote_class)});
    int result = copy_output(response, output, size);
    if (result == 0 && *output && *size) {
        [keys_lock lock];
        [remote_keys addObject:encoded(*output, *size)];
        [keys_lock unlock];
    }
    return result;
}

static int reset_owned_sessions(void) {
    initialize();
    if (!broker_path) return 0;
    // The broker owns the complete handle map, including sessions from an
    // earlier sharingd instance. Cleanup is bounded by one endpoint deadline.
    return status(rpc(@{@"operation": @"reset_owned_sessions"}));
}

static int mirra_session(int bag, unsigned int flags, const void *local, size_t local_size,
                        const void *remote, size_t remote_size, const void *params, size_t params_size, int *session) {
    if (!owns_local(local, local_size))
        return aks_remote_session(bag, flags, local, local_size, remote, remote_size, params, params_size, session);
    unsigned int forwarded_flags = flags;
    const char *compatibility = getenv("MIRRA_ASSERT_DELAY_MODE");
    // This opt-in experiment tests protocol compatibility only. The native
    // macOS credential prompt still runs; no successful authentication is faked.
    if (compatibility && (strcmp(compatibility, "2") == 0 || strcmp(compatibility, "8") == 0) && (flags & 0x3f) == 12)
        forwarded_flags = (flags & ~0x3fu) | (unsigned int)strtoul(compatibility, NULL, 10);
    fprintf(stderr, "MIRRA_KEYSTORE session flags=0x%x forwarded=0x%x local_bytes=%zu remote_bytes=%zu auxiliary_bytes=%zu\n",
            flags, forwarded_flags, local_size, remote_size, params_size);
    NSDictionary *request = @{@"operation": @"session_signed", @"local_ltk": encoded(local, local_size),
                                   @"remote_ltk": encoded(remote, remote_size), @"flags": @(forwarded_flags),
                                   @"parameters": encoded(params, params_size)};
    NSDictionary *response = rpc(request);
    if (status(response) == (int)0xe00002bd && reset_owned_sessions() == 0) {
        response = rpc(request);
    }
    if (status(response) == 0 && session) {
        *session = [response[@"session"] intValue];
    }
    return status(response);
}

static int mirra_step(int session, const void *input, size_t input_size, void **output, size_t *size) {
    if (!virtual_session(session)) return aks_remote_session_step(session, input, input_size, output, size);
    return copy_output(rpc(@{@"operation": @"step", @"session": @(session), @"data": encoded(input, input_size)}), output, size);
}

static int mirra_token(int session, void **output, size_t *size) {
    if (!virtual_session(session)) return aks_remote_session_token(session, output, size);
    return copy_output(rpc(@{@"operation": @"token", @"session": @(session)}), output, size);
}

static int mirra_confirm(int session, void **output, size_t *size) {
    if (!virtual_session(session)) return aks_remote_peer_confirm(session, output, size);
    return copy_output(rpc(@{@"operation": @"confirm", @"session": @(session)}), output, size);
}

static int mirra_setup(int session, const void *input, size_t input_size, void **output, size_t *size) {
    if (!virtual_session(session)) return aks_remote_peer_setup(session, input, input_size, output, size);
    return copy_output(rpc(@{@"operation": @"setup", @"session": @(session), @"data": encoded(input, input_size)}), output, size);
}

static int mirra_reset(int session) {
    if (!virtual_session(session)) return aks_remote_session_reset(session);
    int result = status(rpc(@{@"operation": @"reset", @"session": @(session)}));
    return result;
}

static int mirra_reset_all(void) {
    initialize();
    int owned = reset_owned_sessions();
    // Always preserve the original Mac operation. Report backend failure too,
    // so a successful native reset cannot hide incomplete remote cleanup.
    int native = aks_remote_session_reset_all();
    return owned != 0 ? owned : native;
}

static int mirra_state(int bag, const void *remote, size_t remote_size, void *state) {
    initialize();
    if (!broker_path || !remote || !remote_size || remote_size > 65536)
        return aks_remote_peer_get_state(bag, remote, remote_size, state);
    BOOL known = owns(remote_keys, remote, remote_size);
    NSDictionary *response = rpc(@{@"operation": @"get_state", @"data": encoded(remote, remote_size)});
    if (!known && !response[@"status"]) return aks_remote_peer_get_state(bag, remote, remote_size, state);
    if (status(response) == 0 && !known) {
        [keys_lock lock];
        [remote_keys addObject:encoded(remote, remote_size)];
        [keys_lock unlock];
    }
    NSData *data = [[NSData alloc] initWithBase64EncodedString:response[@"data"] ?: @"" options:0];
    if (state && data.length == 104) memcpy(state, data.bytes, 104);
    return status(response);
}

static int mirra_drop(int bag, const void *remote, size_t remote_size) {
    if (!owns(remote_keys, remote, remote_size)) return aks_remote_peer_drop(bag, remote, remote_size);
    return status(rpc(@{@"operation": @"drop", @"data": encoded(remote, remote_size)}));
}

DYLD_INTERPOSE(mirra_copy_dcrt, MAECopyDCRTMacOSWithError)
DYLD_INTERPOSE(mirra_issue_dcrt, MAEIssueDCRTMacOSWithCallback)
DYLD_INTERPOSE(mirra_create, aks_create_signing_key_with_params)
DYLD_INTERPOSE(mirra_validate, aks_validate_local_key)
DYLD_INTERPOSE(mirra_sign, aks_sign_signing_key)
DYLD_INTERPOSE(mirra_session, aks_remote_session)
DYLD_INTERPOSE(mirra_step, aks_remote_session_step)
DYLD_INTERPOSE(mirra_token, aks_remote_session_token)
DYLD_INTERPOSE(mirra_confirm, aks_remote_peer_confirm)
DYLD_INTERPOSE(mirra_setup, aks_remote_peer_setup)
DYLD_INTERPOSE(mirra_reset, aks_remote_session_reset)
DYLD_INTERPOSE(mirra_reset_all, aks_remote_session_reset_all)
DYLD_INTERPOSE(mirra_state, aks_remote_peer_get_state)
DYLD_INTERPOSE(mirra_drop, aks_remote_peer_drop)
DYLD_INTERPOSE(diagnostic_swift_error, _swift_stdlib_bridgeErrorToNSError)

__attribute__((constructor)) static void loaded(void) {
    initialize();
    if (broker_path) {
        const char *transport = getenv("MIRRA_PAIRING_TRANSPORT");
        if (transport && (strcmp(transport, "wifi") == 0 || strcmp(transport, "wifi-usb") == 0)) {
            // Runtime descriptions on this build verified these bits. Only alter
            // the exact BLE-only authentication request seen in the captured log.
            // Keep account/authentication/paired flags and the crypto exchange intact.
            dlopen("/System/Library/PrivateFrameworks/Rapport.framework/Rapport", RTLD_LAZY);
            Class client = NSClassFromString(@"RPCompanionLinkClient");
            SEL selector = sel_registerName("setControlFlags:");
            Method method = class_getInstanceMethod(client, selector);
            BOOL allowUSB = strcmp(transport, "wifi-usb") == 0;
            if (method && strcmp(method_getTypeEncoding(method), "v24@0:8Q16") == 0) {
                IMP original = method_getImplementation(method);
                IMP replacement = imp_implementationWithBlock(^(id object, uint64_t flags) {
                    uint64_t forwarded = flags;
                    if (flags == UINT64_C(0x40000600102)) {
                        forwarded = (flags & ~UINT64_C(0x600100)) | UINT64_C(0xc);
                        if (allowUSB) forwarded &= ~UINT64_C(0x40000000000);
                        fprintf(stderr, "MIRRA_KEYSTORE pairing_transport original=0x%llx forwarded=0x%llx\n",
                                (unsigned long long)flags, (unsigned long long)forwarded);
                    } else if (flags == UINT64_C(0x8040000220002)) {
                        // The next remote-unlock request forces AWDL and requires
                        // screen-off state. Restore the transport selection that
                        // completed a real unlock in the morning trial. Retain
                        // BLE discovery, NeedsCLinkScreenOff and security bits.
                        forwarded = (flags & ~UINT64_C(0x220000)) | UINT64_C(0xc);
                        if (allowUSB) forwarded &= ~UINT64_C(0x40000000000);
                        fprintf(stderr, "MIRRA_KEYSTORE unlock_transport original=0x%llx forwarded=0x%llx screen_off_check=preserved\n",
                                (unsigned long long)flags, (unsigned long long)forwarded);
                    }
                    ((void (*)(id, SEL, uint64_t))original)(object, selector, forwarded);
                });
                method_setImplementation(method, replacement);
                fprintf(stderr, "MIRRA_KEYSTORE pairing_transport_enabled=%s\n", transport);
            } else {
                fprintf(stderr, "MIRRA_KEYSTORE pairing_transport_unavailable\n");
            }
        }
        for (NSString *name in @[@"SDAuthenticationResponse", @"SDAuthenticationPairingResponse", @"SDAuthenticationConfirmation"]) {
            Class model = NSClassFromString(name);
            Method getter = class_getInstanceMethod(model, sel_registerName("errorCode"));
            if (!getter) continue;
            char *type = method_copyReturnType(getter);
            BOOL supported = type && (strcmp(type,"I") == 0 || strcmp(type,"i") == 0);
            free(type);
            if (!supported) continue;
            IMP original = method_getImplementation(getter);
            IMP replacement = imp_implementationWithBlock(^unsigned int(id object) {
                unsigned int value = ((unsigned int (*)(id,SEL))original)(object,sel_registerName("errorCode"));
                fprintf(stderr,"MIRRA_KEYSTORE protocol_error model=%s code=%u hex=0x%x\n",class_getName(object_getClass(object)),value,value);
                return value;
            });
            method_setImplementation(getter,replacement);
        }
    }
    fprintf(stderr, "MIRRA_KEYSTORE_SHIM_LOADED pid=%d enabled=%d\n", getpid(), broker_path != nil);
}
