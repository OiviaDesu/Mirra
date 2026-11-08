#import <AppKit/AppKit.h>
#import "../src/app/authorization.h"
#include <sys/stat.h>
static int checks;
static void require(BOOL condition, NSString *message) { checks++; if (!condition) { fprintf(stderr,"%s\n",message.UTF8String); exit(1); } }
static void pumpUntil(BOOL (^complete)(void)) {
    NSDate *limit = [NSDate dateWithTimeIntervalSinceNow:8];
    while (!complete() && limit.timeIntervalSinceNow > 0) [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.01]];
    require(complete(), @"Credential exchange timed out");
}
static NSDictionary *invoke(NSString *helper, NSDictionary *env) {
    NSTask *task = [[NSTask alloc] init]; task.executableURL = [NSURL fileURLWithPath:helper]; task.arguments = @[@"Phone SSH password"];
    NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy]; [environment addEntriesFromDictionary:env]; task.environment = environment;
    NSPipe *out = [NSPipe pipe]; NSPipe *err = [NSPipe pipe]; task.standardOutput = out; task.standardError = err;
    require([task launchAndReturnError:NULL], @"Askpass helper did not launch");
    pumpUntil(^BOOL { return !task.running; });
    return @{@"code":@(task.terminationStatus), @"stdout":[out.fileHandleForReading readDataToEndOfFile], @"stderr":[err.fileHandleForReading readDataToEndOfFile]};
}
int main(int argc, char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication]; require(argc == 2, @"Supply the actual askpass helper path");
        NSString *helper = [NSString stringWithUTF8String:argv[1]];
        __block NSString *answer = @"only-test-credential"; __block int prompts = 0;
        Authorization *channel = [[Authorization alloc] initWithPrompt:^(NSString *prompt, void (^reply)(NSString *)) { require([prompt isEqual:@"Phone SSH password"], @"Prompt changed in transit"); prompts++; reply(answer); }];
        require(channel != nil, @"Cannot create password channel");
        NSString *socket = channel.environment[@"MIRRA_ASKPASS_SOCKET"]; struct stat info;
        require(!stat(socket.fileSystemRepresentation,&info) && (info.st_mode & 0777) == 0600, @"Socket is not private");
        require(!stat(socket.stringByDeletingLastPathComponent.fileSystemRepresentation,&info) && (info.st_mode & 0777) == 0700, @"Channel directory is not private");
        NSDictionary *result = invoke(helper, channel.environment);
        require([result[@"code"] intValue] == 0 && [result[@"stdout"] isEqual:[@"only-test-credential\n" dataUsingEncoding:NSUTF8StringEncoding]], @"SSH did not receive the exact credential");
        require([result[@"stderr"] length] == 0, @"Credential helper wrote diagnostics");
        NSMutableDictionary *wrong = [channel.environment mutableCopy]; wrong[@"MIRRA_ASKPASS_TOKEN"] = @"wrong";
        result = invoke(helper,wrong); require([result[@"code"] intValue] != 0 && ![result[@"stdout"] length] && prompts == 1, @"Wrong token reached UI or received a password");
        answer = nil; result = invoke(helper,channel.environment);
        require([result[@"code"] intValue] != 0 && ![result[@"stdout"] length], @"Cancellation leaked a response");
        answer = @""; result = invoke(helper,channel.environment); require([result[@"code"] intValue] == 0 && [result[@"stdout"] isEqual:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]], @"Empty password was not passed faithfully");
        [channel invalidate]; pumpUntil(^BOOL { return ![NSFileManager.defaultManager fileExistsAtPath:socket]; });
        require(![NSFileManager.defaultManager fileExistsAtPath:socket.stringByDeletingLastPathComponent], @"Private channel directory was left behind");
        result = invoke(helper,channel.environment); require([result[@"code"] intValue] != 0 && ![result[@"stdout"] length], @"Expired channel fell back to a popup");
        __block void (^lateAnswer)(NSString *) = nil;
        __block Authorization *pending = nil;
        pending = [[Authorization alloc] initWithPrompt:^(NSString *prompt, void (^reply)(NSString *)) { (void)prompt; lateAnswer = reply; [pending invalidate]; }];
        result = invoke(helper,pending.environment);
        require([result[@"code"] intValue] != 0 && ![result[@"stdout"] length], @"Closing a pending channel did not cancel the credential request");
        require(lateAnswer != nil, @"Pending request did not reach the native UI");
        lateAnswer(@"late-test-credential"); lateAnswer = nil;
        NSString *pendingPath = pending.environment[@"MIRRA_ASKPASS_SOCKET"];
        pumpUntil(^BOOL { return ![NSFileManager.defaultManager fileExistsAtPath:pendingPath]; }); pending = nil;
        printf("{\"passed\":true,\"authorization_checks\":%d,\"real_credentials_used\":false}\n",checks);
    }
}
