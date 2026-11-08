#import "authorization.h"
#import "channel.h"
#include <sys/stat.h>
#include <poll.h>
@interface Authorization ()
@property(readwrite) NSDictionary<NSString *, NSString *> *environment;
@property(atomic) BOOL stopped;
@end
@implementation Authorization
- (instancetype)initWithPrompt:(void (^)(NSString *, void (^)(NSString *)))prompt {
    if (!(self = [super init])) return nil;
    char directory[] = "/tmp/mirra.XXXXXX";
    if (!mkdtemp(directory)) return nil;
    NSString *folder = [NSString stringWithUTF8String:directory];
    NSString *path = [folder stringByAppendingPathComponent:@"password"];
    NSString *token = NSUUID.UUID.UUIDString;
    int listener = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un address = {0}; address.sun_family = AF_UNIX;
    strlcpy(address.sun_path, path.fileSystemRepresentation, sizeof(address.sun_path));
    if (listener < 0 || bind(listener, (struct sockaddr *)&address, sizeof(address)) || chmod(path.fileSystemRepresentation, 0600) || listen(listener, 4)) {
        if (listener >= 0) close(listener); unlink(path.fileSystemRepresentation); rmdir(directory); return nil;
    }
    self.environment = @{@"MIRRA_ASKPASS_SOCKET":path, @"MIRRA_ASKPASS_TOKEN":token};
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        while (!self.stopped) {
            struct pollfd event = {listener, POLLIN, 0};
            if (poll(&event, 1, 200) <= 0) continue;
            int peer = accept(listener, NULL, NULL); if (peer < 0) continue;
            MirraSocketOptions(peer, 3);
            uid_t uid; gid_t gid;
            NSDictionary *request = getpeereid(peer, &uid, &gid) == 0 && uid == getuid() ? MirraReceive(peer) : nil;
            if (![request[@"token"] isEqual:token]) { close(peer); continue; }
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            NSMutableDictionary *response = [NSMutableDictionary dictionary];
            __block BOOL accepting = YES;
            dispatch_async(dispatch_get_main_queue(), ^{
                if (self.stopped) { dispatch_semaphore_signal(done); return; }
                NSString *message = [request[@"prompt"] isKindOfClass:NSString.class] ? request[@"prompt"] : @"SSH password";
                prompt(message, ^(NSString *password) {
                    @synchronized (response) { if (accepting && password) response[@"password"] = password; }
                    dispatch_semaphore_signal(done);
                });
            });
            long result = 1;
            for (int i = 0; i < 1100 && !self.stopped; i++) {
                result = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 100 * NSEC_PER_MSEC));
                if (!result) break;
            }
            NSDictionary *reply;
            @synchronized (response) {
                accepting = NO;
                reply = !result && response[@"password"] && !self.stopped ? [response copy] : @{@"cancel":@YES};
                [response removeAllObjects];
            }
            MirraSend(peer, reply); close(peer);
        }
        close(listener); unlink(path.fileSystemRepresentation); rmdir(folder.fileSystemRepresentation);
    });
    return self;
}
- (void)invalidate { self.stopped = YES; }
@end
