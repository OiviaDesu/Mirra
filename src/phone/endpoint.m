#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <TargetConditionals.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <CommonCrypto/CommonDigest.h>

// Experimental AKS endpoint over stdin/stdout (carried by authenticated SSH).
// All identities live in newly allocated unsaved keybags. Only session IDs
// created by this process can be stepped/reset. System keybags are not used.
typedef int (*CreateBag)(const void *, int, int, int *);
typedef int (*UnloadBag)(int);
typedef int (*CreateKey)(int, int, void **, size_t *);
typedef int (*CreateKeyParams)(int, int, const void *, size_t, void **, size_t *);
typedef int (*SignKey)(int, int, const void *, size_t, int, const void *, size_t, void **, size_t *);
typedef void *(*ParamsCreate)(const void *, size_t);
typedef int (*ParamsData)(void *, int, const void *, size_t);
typedef int (*ParamsNumber)(void *, int, const int64_t *);
typedef int (*ParamsDER)(void *, const void **, size_t *);
typedef int (*RemoteSession)(int, unsigned int, const void *, size_t, const void *, size_t, const void *, size_t, int *);
typedef int (*RemoteStep)(int, const void *, size_t, void **, size_t *);
typedef int (*ResetSession)(int);
typedef int (*RemoteOutput)(int, void **, size_t *);
typedef int (*ValidateKey)(int, int, const void *, size_t);
typedef int (*PeerState)(int, const void *, size_t, void *);
typedef int (*PeerDrop)(int, const void *, size_t);

static void reply(NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:NULL];
    fwrite(data.bytes, 1, data.length, stdout);
    putchar('\n');
    fflush(stdout);
}

static void diagnostic(NSDictionary *value) {
    // stderr is diagnostic-only; the JSON request/reply stream stays intact.
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:NULL];
    NSMutableData *line = [[@"MIRRA_ENDPOINT " dataUsingEncoding:NSUTF8StringEncoding] mutableCopy];
    [line appendData:data]; [line appendBytes:"\n" length:1];
    // Use the already-reviewed write import in the fallback iOS linker.
    (void)write(STDERR_FILENO, line.bytes, line.length);
}

static NSMutableDictionary *result(int status) {
    return [@{@"status": @(status), @"status_hex": [NSString stringWithFormat:@"0x%08x", status]} mutableCopy];
}

static NSDictionary *reset_owned_session(NSMutableSet<NSNumber *> *sessions,
                                         NSNumber *session, ResetSession reset) {
    if (!session || ![sessions containsObject:session])
        return @{@"error": @"session_not_owned"};
    int status = reset(session.intValue);
    // A failed AKS reset may leave the native session alive. Retain its handle
    // for retry and final process cleanup instead of silently orphaning it.
    if (status == 0) [sessions removeObject:session];
    return result(status);
}

static void clear_blob(void *blob, size_t size) {
    if (!blob) return;
    volatile unsigned char *bytes = blob;
    for (size_t i = 0; i < size; i++) bytes[i] = 0;
    free(blob);
}

static NSString *digest(NSData *data) {
    if (!data.length || data.length > 1048576) return @"";
    unsigned char value[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, value);
    char text[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    for (size_t i = 0; i < sizeof(value); i++) snprintf(text + i * 2, 3, "%02x", value[i]);
    return [NSString stringWithUTF8String:text];
}

static NSString *system_value(const char *name) {
    char value[256] = {0};
    size_t size = sizeof(value);
    return sysctlbyname(name, value, &size, NULL, 0) == 0 ? [NSString stringWithUTF8String:value] : @"unavailable";
}

static void *required_symbol(void *library, const char *name,
                             NSMutableArray<NSString *> *missing,
                             void *(*resolve)(void *, const char *)) {
    void *symbol = library ? resolve(library, name) : NULL;
    if (!symbol) [missing addObject:@(name)];
    return symbol;
}

static NSString *process_architecture(void) {
#if defined(__arm64e__)
    return @"arm64e";
#elif defined(__arm64__)
    return @"arm64";
#elif defined(__x86_64__)
    return @"x86_64";
#else
    return @"unknown";
#endif
}

static int export_certificate(const char *output, BOOL issue) {
    diagnostic(@{@"operation": issue ? @"issue_certificate" : @"export_certificate", @"stage": @"begin"});
#if TARGET_OS_IPHONE
    void *ma = dlopen("/System/Library/PrivateFrameworks/MobileActivation.framework/MobileActivation", RTLD_LAZY | RTLD_LOCAL);
    typedef NSData *(*CopyDCRT)(NSError *__autoreleasing *);
    CopyDCRT copy = ma ? (CopyDCRT)dlsym(ma, "MAECopyDCRTWithError") : NULL;
    diagnostic(@{@"stage": @"resolve_certificate_api", @"framework_loaded": @(ma != NULL), @"copy_available": @(copy != NULL)});
    if (!copy) { reply(@{@"error": @"certificate_api_unavailable"}); return 1; }
    struct stat info;
    if (lstat(output, &info) == 0) { reply(@{@"error": @"output_already_exists"}); return 1; }
    if (issue) {
        typedef void (*IssueDCRT)(NSDictionary *, void *, void (^)(NSError *));
        IssueDCRT request = (IssueDCRT)dlsym(ma, "MAEIssueDCRTWithCallback");
        diagnostic(@{@"stage": @"resolve_issuance_api", @"issue_available": @(request != NULL)});
        if (!request) { reply(@{@"error": @"certificate_issuance_api_unavailable"}); return 1; }
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        __block NSError *issue_error = nil;
        diagnostic(@{@"stage": @"MAEIssueDCRTWithCallback_call"});
        request(nil, NULL, ^(NSError *error) {
            issue_error = error;
            diagnostic(@{@"stage": @"MAEIssueDCRTWithCallback_result", @"error_code": @(error.code), @"error_domain": error.domain ?: @""});
            dispatch_semaphore_signal(done);
        });
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:40];
        BOOL completed = NO;
        while (deadline.timeIntervalSinceNow > 0) {
            if (dispatch_semaphore_wait(done, DISPATCH_TIME_NOW) == 0) { completed = YES; break; }
            [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
        }
        if (!completed || issue_error) {
            diagnostic(@{@"stage": @"issuance_failed", @"completed": @(completed), @"error_code": @(issue_error.code)});
            reply(@{@"error": @"certificate_issuance_failed", @"completed": @(completed),
                    @"error_code": @(issue_error.code), @"error_domain": issue_error.domain ?: @""});
            return 1;
        }
    }
    NSError *error = nil;
    diagnostic(@{@"stage": @"MAECopyDCRTWithError_call"});
    NSData *data = copy(&error);
    NSUInteger certificate_bytes = [data isKindOfClass:NSData.class] ? data.length : 0;
    diagnostic(@{@"stage": @"MAECopyDCRTWithError_result", @"bytes": @(certificate_bytes), @"error_code": @(error.code), @"error_domain": error.domain ?: @""});
    if (![data isKindOfClass:NSData.class] || !data.length || data.length > 1048576) {
        reply(@{@"error": @"cached_certificate_unavailable", @"error_code": @(error.code),
                @"error_domain": error.domain ?: @""});
        return 1;
    }
    int fd = open(output, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0) { reply(@{@"error": @"certificate_output_unavailable"}); return 1; }
    const uint8_t *bytes = data.bytes;
    size_t written = 0;
    while (written < data.length) {
        ssize_t count = write(fd, bytes + written, data.length - written);
        if (count <= 0) { close(fd); unlink(output); reply(@{@"error": @"certificate_write_failed"}); return 1; }
        written += (size_t)count;
    }
    close(fd);
    diagnostic(@{@"stage": @"certificate_file_written", @"bytes": @(written), @"issuance_requested": @(issue)});
    reply(@{@"certificate_exported": @YES, @"bytes": @(data.length), @"sha256": digest(data), @"issuance_requested": @(issue)});
    return 0;
#else
    (void)output; (void)issue;
    reply(@{@"error": @"certificate_export_requires_ios_backend"});
    return 1;
#endif
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 3 && (!strcmp(argv[1], "--export-certificate") || !strcmp(argv[1], "--issue-certificate")))
            return export_certificate(argv[2], !strcmp(argv[1], "--issue-certificate"));
        BOOL capabilities = argc == 2 && !strcmp(argv[1], "--capabilities");
        if (argc != 2) { reply(@{@"ready": @NO, @"error": @"certificate_path_or_capabilities_required"}); return 1; }
#if TARGET_OS_IPHONE
        const char *path = "/System/Library/PrivateFrameworks/AppleKeyStore.framework/AppleKeyStore";
#else
        const char *path = "/System/Library/PrivateFrameworks/AppleKeyStore.framework/Versions/A/AppleKeyStore";
#endif
        void *library = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
        NSMutableArray<NSString *> *missing = [NSMutableArray array];
#define LOAD(TYPE, NAME, SYMBOL) TYPE NAME = (TYPE)required_symbol(library, SYMBOL, missing, dlsym)
        LOAD(CreateBag, create_bag, "aks_create_bag");
        LOAD(UnloadBag, unload_bag, "aks_unload_bag");
        LOAD(CreateKey, create_key, "aks_create_signing_key");
        LOAD(CreateKeyParams, create_key_params, "aks_create_signing_key_with_params");
        LOAD(SignKey, sign_key, "aks_sign_signing_key");
        LOAD(ParamsCreate, params_create, "aks_params_create");
        LOAD(ParamsData, params_data, "aks_params_set_data");
        LOAD(ParamsNumber, params_number, "aks_params_set_number");
        LOAD(ParamsDER, params_der, "aks_params_get_der");
        LOAD(RemoteSession, remote_session, "aks_remote_session");
        LOAD(RemoteStep, remote_step, "aks_remote_session_step");
        LOAD(ResetSession, reset_session, "aks_remote_session_reset");
        LOAD(RemoteOutput, session_token, "aks_remote_session_token");
        LOAD(RemoteOutput, peer_confirm, "aks_remote_peer_confirm");
        LOAD(RemoteStep, peer_setup, "aks_remote_peer_setup");
        LOAD(ValidateKey, validate_key, "aks_validate_local_key");
        LOAD(PeerState, peer_state, "aks_remote_peer_get_state");
        LOAD(PeerDrop, peer_drop, "aks_remote_peer_drop");
#undef LOAD
        BOOL available = missing.count == 0;
        NSMutableDictionary *capability_report = [@{@"protocol_version": @1,
            @"required_symbols_available": @(available), @"missing_symbols": missing,
            @"process_architecture": process_architecture(), @"minimum_ios": @"15.0",
            @"product_type": system_value("hw.machine"), @"os_build": system_value("kern.osversion"),
            @"os_version": system_value("kern.osproductversion"), @"end_to_end_verified": @NO} mutableCopy];
        if (!available) {
            capability_report[@"ready"] = @NO;
            capability_report[@"error"] = library ? @"required_keystore_symbols_unavailable" : @"keystore_library_unavailable";
        }
        if (capabilities) {
            reply(capability_report);
            return available ? 0 : 1;
        }
        if (!available) {
            reply(capability_report);
            return 1;
        }
        NSData *dcrt = argc == 2 ? [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:argv[1]]] : nil;
        if (!dcrt.length || dcrt.length > 1048576) { reply(@{@"ready": @NO, @"error": @"device_certificate_unavailable"}); return 1; }
        NSMutableDictionary<NSString *, NSDictionary *> *identities = [NSMutableDictionary dictionary];
        NSMutableSet<NSNumber *> *sessions = [NSMutableSet set];
        NSMutableDictionary<NSString *, NSDictionary *> *remote_owners = [NSMutableDictionary dictionary];
        capability_report[@"ready"] = @YES;
        capability_report[@"dcrt_bytes"] = @(dcrt.length);
        capability_report[@"dcrt_sha256"] = digest(dcrt);
        reply(capability_report);
        char *line = NULL;
        size_t capacity = 0;
        ssize_t count;
        while ((count = getline(&line, &capacity, stdin)) > 0) {
            @autoreleasepool {
                if (count > 262144) {
                    reply(@{@"error": @"request_too_large"});
                    break;
                }
                NSDictionary *request = [NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:line length:(NSUInteger)count] options:0 error:NULL];
                if (![request isKindOfClass:NSDictionary.class]) {
                    reply(@{@"error": @"invalid_json"});
                    continue;
                }
                NSString *operation = request[@"operation"];
                if ([operation isEqual:@"exit"]) {
                    reply(@{@"closed": @YES});
                    break;
                }
                if ([operation isEqual:@"generate"]) {
                    BOOL attested = [request[@"attested"] boolValue];
                    int key_class = request[@"key_class"] ? [request[@"key_class"] intValue] : 9;
                    if (key_class != 9 && key_class != 10 && key_class != 11) {
                        reply(@{@"error": @"invalid_key_class"});
                        continue;
                    }
                    if (identities.count >= 8 || (attested && !dcrt.length)) {
                        reply(@{@"error": @"identity_limit_or_missing_dcrt"});
                        continue;
                    }
                    uint8_t passcode[32];
                    if (SecRandomCopyBytes(kSecRandomDefault, sizeof(passcode), passcode) != 0) return 1;
                    int bag = -1;
                    int status = create_bag(passcode, sizeof(passcode), 0, &bag);
                    memset(passcode, 0, sizeof(passcode));
                    void *blob = NULL;
                    size_t size = 0;
                    if (status == 0 && bag >= 0) {
                        if (!attested) {
                            status = create_key(bag, key_class, &blob, &size);
                        } else {
                            void *params = params_create(NULL, 0);
                            int64_t flag = 4;
                            const void *der = NULL;
                            size_t der_size = 0;
                            status = params_data(params, 21, dcrt.bytes, dcrt.length);
                            if (status == 0) status = params_number(params, 20, &flag);
                            if (status == 0) status = params_der(params, &der, &der_size);
                            if (status == 0) status = create_key_params(bag, key_class, der, der_size, &blob, &size);
                        }
                    }
                    NSMutableDictionary *response = result(status);
                    if (status == 0 && blob && size && size <= 65536) {
                        NSString *identifier = NSUUID.UUID.UUIDString;
                        NSData *ltk = [NSData dataWithBytes:blob length:size];
                        identities[identifier] = @{@"bag": @(bag), @"ltk": ltk, @"attested": @(attested), @"key_class": @(key_class)};
                        response[@"identity"] = identifier;
                        response[@"ltk"] = [ltk base64EncodedStringWithOptions:0];
                        response[@"bytes"] = @(size);
                        response[@"key_class"] = @(key_class);
                    } else if (bag >= 0) {
                        unload_bag(bag);
                    }
                    clear_blob(blob, size);
                    reply(response);
                    continue;
                }
                if ([operation isEqual:@"validate"]) {
                    NSDictionary *identity = identities[request[@"identity"] ?: @""];
                    if (!identity) {
                        reply(@{@"error": @"identity_not_owned"});
                        continue;
                    }
                    NSData *key = identity[@"ltk"];
                    reply(result(validate_key([identity[@"bag"] intValue], [identity[@"key_class"] intValue], key.bytes, key.length)));
                    continue;
                }
                if ([operation isEqual:@"get_state"] || [operation isEqual:@"drop"]) {
                    NSString *encoded = request[@"data"];
                    NSDictionary *identity = remote_owners[encoded ?: @""];
                    NSData *key = [encoded isKindOfClass:NSString.class] ? [[NSData alloc] initWithBase64EncodedString:encoded options:0] : nil;
                    if (!identity || !key.length) {
                        reply(@{@"error": @"remote_key_not_owned"});
                        continue;
                    }
                    if ([operation isEqual:@"drop"]) {
                        int status = peer_drop([identity[@"bag"] intValue], key.bytes, key.length);
                        if (status == 0) [remote_owners removeObjectForKey:encoded];
                        reply(result(status));
                    } else {
                        uint8_t state[104] = {0};
                        int status = peer_state([identity[@"bag"] intValue], key.bytes, key.length, state);
                        NSMutableDictionary *response = result(status);
                        response[@"data"] = [[NSData dataWithBytes:state length:sizeof(state)] base64EncodedStringWithOptions:0];
                        response[@"bytes"] = @(sizeof(state));
                        reply(response);
                    }
                    continue;
                }
                if ([operation isEqual:@"session"] || [operation isEqual:@"session_signed"] || [operation isEqual:@"sign_remote"]) {
                    NSDictionary *identity = identities[request[@"identity"] ?: @""];
                    NSString *encoded = request[@"remote_ltk"];
                    NSData *remote = [encoded isKindOfClass:NSString.class] ? [[NSData alloc] initWithBase64EncodedString:encoded options:0] : nil;
                    if (!identity || !remote.length || remote.length > 65536 || sessions.count >= 16) {
                        reply(@{@"error": @"invalid_identity_key_or_session_limit"});
                        continue;
                    }
                    NSData *local = identity[@"ltk"];
                    unsigned int flags = [request[@"flags"] unsignedIntValue];
                    NSString *params_encoded = request[@"parameters"];
                    NSData *session_params = [params_encoded isKindOfClass:NSString.class] ? [[NSData alloc] initWithBase64EncodedString:params_encoded options:0] : nil;
                    if (session_params.length > 65536) {
                        reply(@{@"error": @"parameters_too_large"});
                        continue;
                    }
                    int remote_class = request[@"remote_key_class"] ? [request[@"remote_key_class"] intValue] : 9;
                    if (remote_class != 9 && remote_class != 10 && remote_class != 11) {
                        reply(@{@"error": @"invalid_remote_key_class"});
                        continue;
                    }
                    void *signed_key = NULL;
                    size_t signed_size = 0;
                    int sign_status = 0;
                    if ([operation isEqual:@"session_signed"]) {
                        if (remote_owners[encoded] != identity) {
                            reply(@{@"error": @"signed_remote_key_not_owned"});
                            continue;
                        }
                        signed_size = remote.length;
                        signed_key = malloc(signed_size);
                        if (!signed_key) {
                            reply(result((int)0xe00002bd));
                            continue;
                        }
                        memcpy(signed_key, remote.bytes, signed_size);
                    } else {
                        sign_status = sign_key([identity[@"bag"] intValue], [identity[@"key_class"] intValue],
                                               local.bytes, local.length, remote_class, remote.bytes, remote.length,
                                               &signed_key, &signed_size);
                        if (sign_status == 0 && signed_key && signed_size)
                            remote_owners[[[NSData dataWithBytes:signed_key length:signed_size] base64EncodedStringWithOptions:0]] = identity;
                    }
                    if ([operation isEqual:@"sign_remote"]) {
                        NSMutableDictionary *response = result(sign_status);
                        if (sign_status == 0 && signed_key && signed_size) {
                            response[@"data"] = [[NSData dataWithBytes:signed_key length:signed_size] base64EncodedStringWithOptions:0];
                            response[@"bytes"] = @(signed_size);
                        }
                        clear_blob(signed_key, signed_size);
                        reply(response);
                        continue;
                    }
                    int session = -1;
                    int status = sign_status;
                    if (sign_status == 0 && signed_key && signed_size)
                        status = remote_session([identity[@"bag"] intValue], flags,
                                                local.bytes, local.length, signed_key, signed_size,
                                                session_params.bytes, session_params.length, &session);
                    NSMutableDictionary *response = result(status);
                    response[@"sign_status_hex"] = [NSString stringWithFormat:@"0x%08x", sign_status];
                    response[@"signed_bytes"] = @(signed_size);
                    clear_blob(signed_key, signed_size);
                    if (status == 0 && session >= 0) {
                        [sessions addObject:@(session)];
                        response[@"session"] = @(session);
                    }
                    reply(response);
                    continue;
                }
                if ([operation isEqual:@"step"] || [operation isEqual:@"reset"] ||
                    [operation isEqual:@"token"] || [operation isEqual:@"confirm"] || [operation isEqual:@"setup"]) {
                    NSNumber *session = request[@"session"];
                    if (!session || ![sessions containsObject:session]) {
                        reply(@{@"error": @"session_not_owned"});
                        continue;
                    }
                    if ([operation isEqual:@"reset"]) {
                        reply(reset_owned_session(sessions, session, reset_session));
                        continue;
                    }
                    NSString *encoded = request[@"data"];
                    NSData *input = [encoded isKindOfClass:NSString.class] ? [[NSData alloc] initWithBase64EncodedString:encoded options:0] : nil;
                    if (input.length > 65536) {
                        reply(@{@"error": @"token_too_large"});
                        continue;
                    }
                    void *output = NULL;
                    size_t size = 0;
                    int status;
                    if ([operation isEqual:@"token"])
                        status = session_token(session.intValue, &output, &size);
                    else if ([operation isEqual:@"confirm"])
                        status = peer_confirm(session.intValue, &output, &size);
                    else if ([operation isEqual:@"setup"])
                        status = peer_setup(session.intValue, input.bytes, input.length, &output, &size);
                    else
                        status = remote_step(session.intValue, input.bytes, input.length, &output, &size);
                    NSMutableDictionary *response = result(status);
                    if (output && size && size <= 65536) {
                        NSData *data = [NSData dataWithBytes:output length:size];
                        response[@"data"] = [data base64EncodedStringWithOptions:0];
                    }
                    response[@"bytes"] = @(size);
                    clear_blob(output, size);
                    reply(response);
                    continue;
                }
                reply(@{@"error": @"unknown_operation"});
            }
        }
        free(line);
        for (NSNumber *session in sessions) reset_session(session.intValue);
        for (NSDictionary *identity in identities.allValues) unload_bag([identity[@"bag"] intValue]);
    }
    return 0;
}
