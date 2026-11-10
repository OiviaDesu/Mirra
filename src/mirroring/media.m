#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import "profile.h"
#import "awdl-service.h"
#import <Network/Network.h>
#include <dlfcn.h>
#include <stdio.h>
#include <net/if.h>
#include <netinet/in.h>
#include <os/log.h>
#include <stdarg.h>
#include <pthread.h>
#include <ifaddrs.h>
#include <arpa/inet.h>

static pthread_mutex_t peerLock = PTHREAD_MUTEX_INITIALIZER;
static NSHashTable *udpListeners;
static bool mediaEnabled;

static bool local_awdl_address(struct sockaddr_in6 *address) {
    struct ifaddrs *all = NULL;
    bool found = false;
    if (getifaddrs(&all) != 0) return false;
    for (struct ifaddrs *item = all; item; item = item->ifa_next) {
        if (strcmp(item->ifa_name, "awdl0") || !item->ifa_addr || item->ifa_addr->sa_family != AF_INET6) continue;
        const struct sockaddr_in6 *candidate = (const struct sockaddr_in6 *)item->ifa_addr;
        if (!IN6_IS_ADDR_LINKLOCAL(&candidate->sin6_addr)) continue;
        *address = *candidate;
        address->sin6_scope_id = if_nametoindex("awdl0");
        found = address->sin6_scope_id != 0;
        break;
    }
    freeifaddrs(all);
    return found;
}

static void media_log(const char *format, ...) {
    char line[1024];
    va_list arguments;
    va_start(arguments, format);
    vsnprintf(line, sizeof(line), format, arguments);
    va_end(arguments);
    fputs(line, stderr);
    os_log_with_type(OS_LOG_DEFAULT, OS_LOG_TYPE_DEFAULT, "%{public}s", line);
}

extern NW_RETURNS_RETAINED nw_interface_t nw_interface_create_with_index(uint32_t);
// Endpoint/parameters wrapper, inspected on the reference build and linked
// against the Sequoia SDK as well. Create ownership is retained. Symbol checks
// alone do not establish behavior on every macOS release; see the probe report.
extern NW_RETURNS_RETAINED id nw_path_create_evaluator_for_endpoint(nw_endpoint_t, nw_parameters_t);

#define INTERPOSE(REPLACEMENT, ORIGINAL) \
    __attribute__((used)) static const struct { const void *replacement; const void *original; } \
    interpose_##ORIGINAL __attribute__((section("__DATA,__interpose"))) = \
    {(const void *)(REPLACEMENT), (const void *)(ORIGINAL)};

static bool allow_udp_p2p(nw_parameters_t parameters, const char *site) {
    if (!mediaEnabled || !parameters) return false;
    nw_protocol_stack_t stack = nw_parameters_copy_default_protocol_stack(parameters);
    nw_protocol_options_t options = nw_protocol_stack_copy_transport_protocol(stack);
    if (!options) return false;
    nw_protocol_definition_t definition = nw_protocol_options_copy_definition(options);
    if (nw_protocol_definition_is_equal(definition, nw_protocol_copy_udp_definition())) {
        bool before = nw_parameters_get_include_peer_to_peer(parameters);
        nw_parameters_set_include_peer_to_peer(parameters, true);
        // RP's outgoing path reuses the listening port. Both sides of that
        // bind must permit reuse when Network falls back to BSD sockets.
        nw_parameters_set_reuse_local_address(parameters, true);
        unsigned int index = if_nametoindex("awdl0");
        if (index) {
            nw_interface_t interface = nw_interface_create_with_index(index);
            if (interface) nw_parameters_require_interface(parameters, interface);
        }
        media_log("MEDIA_WORKSPACE udp_p2p site=%s before=%d after=1 awdl_index=%u\n", site, before, index);
        return true;
    }
    return false;
}

static NW_RETURNS_RETAINED nw_listener_t media_listener(nw_parameters_t parameters) {
    bool udp = allow_udp_p2p(parameters, "listener");
    nw_listener_t listener = nw_listener_create(parameters);
    if (udp && listener) {
        pthread_mutex_lock(&peerLock);
        if (!udpListeners) udpListeners = [NSHashTable weakObjectsHashTable];
        [udpListeners addObject:listener];
        pthread_mutex_unlock(&peerLock);
    }
    return listener;
}
static NW_RETURNS_RETAINED nw_connection_t media_connection(nw_endpoint_t endpoint, nw_parameters_t parameters) {
    allow_udp_p2p(parameters, "connection");
    return nw_connection_create(endpoint, parameters);
}
INTERPOSE(media_listener, nw_listener_create)
INTERPOSE(media_connection, nw_connection_create)

// Rapport first reserves and advertises a listener port, then creates an
// outgoing media flow with that same source port. Network's BSD fallback on
// this host rejects the second bind even with reuse_local_address. Relinquish
// only the matching UDP reservation after the phone has returned its endpoint;
// the media flow retains the originally advertised source port. The installed
// Rapport listener event block ignores a caller cancellation (state4, no error).
static void handoff_listener_port(nw_parameters_t parameters) {
    nw_endpoint_t local = nw_parameters_copy_local_endpoint(parameters);
    const struct sockaddr *address = local ? nw_endpoint_get_address(local) : NULL;
    if (!address || address->sa_family != AF_INET6) return;
    uint16_t port = ntohs(((const struct sockaddr_in6 *)address)->sin6_port);
    if (!port) return;
    nw_listener_t reservation = nil;
    pthread_mutex_lock(&peerLock);
    for (nw_listener_t listener in [udpListeners allObjects]) {
        if (nw_listener_get_port(listener) == port) {
            reservation = listener;
            [udpListeners removeObject:listener];
            break;
        }
    }
    pthread_mutex_unlock(&peerLock);
    if (reservation) {
        nw_listener_cancel(reservation);
        media_log("MEDIA_WORKSPACE listener_port_handoff port=%u advertised_port_preserved=1\n", port);
    } else media_log("MEDIA_WORKSPACE listener_port_handoff_missing port=%u\n", port);
}

static NW_RETURNS_RETAINED id media_path(nw_endpoint_t endpoint, nw_parameters_t parameters) {
    if (!mediaEnabled) return nw_path_create_evaluator_for_endpoint(endpoint, parameters);
    bool udp = allow_udp_p2p(parameters, "path_evaluator");
    const struct sockaddr *address = endpoint ? nw_endpoint_get_address(endpoint) : NULL;
    if (address && address->sa_family == AF_INET6) {
        struct sockaddr_in6 scoped = *(const struct sockaddr_in6 *)address;
        if (IN6_IS_ADDR_LINKLOCAL(&scoped.sin6_addr) && !scoped.sin6_scope_id) {
            scoped.sin6_scope_id = if_nametoindex("awdl0");
            if (scoped.sin6_scope_id) endpoint = nw_endpoint_create_address((struct sockaddr *)&scoped);
        }
        if (udp) {
            nw_endpoint_t originalLocal = nw_parameters_copy_local_endpoint(parameters);
            const struct sockaddr *localAddress = originalLocal ? nw_endpoint_get_address(originalLocal) : NULL;
            struct sockaddr_in6 source;
            if (localAddress && localAddress->sa_family == AF_INET6 && local_awdl_address(&source)) {
                source.sin6_port = ((const struct sockaddr_in6 *)localAddress)->sin6_port;
                nw_parameters_set_local_endpoint(parameters, nw_endpoint_create_address((struct sockaddr *)&source));
            }
            handoff_listener_port(parameters);
        }
    }
    return nw_path_create_evaluator_for_endpoint(endpoint, parameters);
}
INTERPOSE(media_path, nw_path_create_evaluator_for_endpoint)

__attribute__((constructor)) static void loaded(void) {
    @autoreleasepool {
        mediaEnabled = MirroringInstallMediaProfile();
        if (mediaEnabled && MirraRepairAWDLService())
            media_log("MEDIA_WORKSPACE oclp_remote_screen_service_repaired=1\n");
        media_log("MIRRA_MEDIA_PROFILE_%s profile=%s\n", mediaEnabled ? "READY" : "UNSUPPORTED", MIRRA_MEDIA_PROFILE);
    }
}
