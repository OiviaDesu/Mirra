#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <arpa/inet.h>
static inline BOOL MirraTransfer(int fd, void *bytes, size_t count, BOOL writing) {
    while (count) {
        ssize_t size = writing ? send(fd, bytes, count, 0) : recv(fd, bytes, count, 0);
        if (size < 0 && errno == EINTR) continue;
        if (size <= 0) return NO;
        bytes = (char *)bytes + size; count -= (size_t)size;
    }
    return YES;
}
static inline BOOL MirraSend(int fd, NSDictionary *value) {
    NSMutableData *data = [[NSJSONSerialization dataWithJSONObject:value options:0 error:NULL] mutableCopy];
    if (!data || data.length > 8192) return NO;
    uint32_t length = htonl((uint32_t)data.length);
    BOOL ok = MirraTransfer(fd, &length, 4, YES) && MirraTransfer(fd, data.mutableBytes, data.length, YES);
    [data resetBytesInRange:NSMakeRange(0, data.length)]; return ok;
}
static inline NSDictionary *MirraReceive(int fd) {
    uint32_t length = 0; if (!MirraTransfer(fd, &length, 4, NO)) return nil;
    length = ntohl(length); if (length == 0 || length > 8192) return nil;
    NSMutableData *data = [NSMutableData dataWithLength:length];
    if (!MirraTransfer(fd, data.mutableBytes, length, NO)) return nil;
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    [data resetBytesInRange:NSMakeRange(0, data.length)];
    return [value isKindOfClass:NSDictionary.class] ? value : nil;
}
static inline void MirraSocketOptions(int fd, int seconds) {
    int yes = 1; setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
    struct timeval timeout = {seconds, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
}
