// Run the production certificate workflow with isolated local API fixtures.
// No phone, real certificate issuer, or system keybag is contacted.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <TargetConditionals.h>
#include <dlfcn.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>

static int mode, copies, issues, checks;
static BOOL liveBeforeCallback;
static NSString *diagnosticsPath;
static NSData *fixture;

static NSData *fixture_copy(NSError *__autoreleasing *error) {
    copies++;
    if (mode == 3) { *error = [NSError errorWithDomain:@"fixture.activation" code:101 userInfo:nil]; return nil; }
    if (mode == 4) return (id)NSNull.null;
    return fixture;
}
static void fixture_issue(NSDictionary *options, void *context, void (^reply)(NSError *)) {
    (void)options; (void)context; issues++;
    NSString *before = [NSString stringWithContentsOfFile:diagnosticsPath encoding:NSUTF8StringEncoding error:NULL];
    liveBeforeCallback = [before containsString:@"MAEIssueDCRTWithCallback_call"] && ![before containsString:@"MAEIssueDCRTWithCallback_result"];
    reply(mode == 2 ? [NSError errorWithDomain:@"fixture.activation" code:42 userInfo:nil] : nil);
}
static void *fixture_open(const char *path, int flags) { (void)path; (void)flags; return (void *)1; }
static void *fixture_symbol(void *handle, const char *name) {
    (void)handle;
    if (strcmp(name,"MAECopyDCRTWithError") == 0) return mode == 5 ? NULL : (void *)fixture_copy;
    if (strcmp(name,"MAEIssueDCRTWithCallback") == 0) return mode == 6 ? NULL : (void *)fixture_issue;
    return NULL;
}
#undef TARGET_OS_IPHONE
#define TARGET_OS_IPHONE 1
#define dlopen fixture_open
#define dlsym fixture_symbol
#define main mirra_endpoint_main
#import "../src/phone/endpoint.m"
#undef main
#undef dlopen
#undef dlsym

static void require(BOOL condition, NSString *message) {
    checks++;
    if (!condition) { fprintf(stderr,"%s\n",message.UTF8String); exit(1); }
}

int main(void) {
    @autoreleasepool {
        char directory[] = "/tmp/mirra-cert-checks-XXXXXX";
        require(mkdtemp(directory) != NULL, @"Cannot create isolated certificate fixture");
        NSString *root = @(directory);
        fixture = [@"fixture certificate bytes" dataUsingEncoding:NSUTF8StringEncoding];
        for (mode=0; mode<7; mode++) {
            copies=issues=0; liveBeforeCallback=NO;
            NSString *output = [root stringByAppendingPathComponent:[NSString stringWithFormat:@"%d.der",mode]];
            NSString *replyPath = [output stringByAppendingString:@".stdout"];
            diagnosticsPath = [output stringByAppendingString:@".stderr"];
            int out = open(replyPath.fileSystemRepresentation,O_WRONLY|O_CREAT|O_EXCL,0600);
            int err = open(diagnosticsPath.fileSystemRepresentation,O_WRONLY|O_CREAT|O_EXCL,0600);
            int savedOut=dup(STDOUT_FILENO), savedErr=dup(STDERR_FILENO);
            require(out>=0 && err>=0 && savedOut>=0 && savedErr>=0, @"Cannot capture the isolated reply channels");
            dup2(out,STDOUT_FILENO); dup2(err,STDERR_FILENO); close(out); close(err);
            int code = export_certificate(output.fileSystemRepresentation,mode==1 || mode==2 || mode==6);
            fflush(stdout); dup2(savedOut,STDOUT_FILENO); dup2(savedErr,STDERR_FILENO); close(savedOut); close(savedErr);
            NSData *wire = [NSData dataWithContentsOfFile:replyPath];
            NSDictionary *value = [NSJSONSerialization JSONObjectWithData:wire options:0 error:NULL];
            require([value isKindOfClass:NSDictionary.class], @"Progress output corrupted the JSON reply channel");
            NSString *logs = [NSString stringWithContentsOfFile:diagnosticsPath encoding:NSUTF8StringEncoding error:NULL];
            require([logs containsString:@"resolve_certificate_api"], @"The actual API-resolution step was not logged");
            require(![logs containsString:@"fixture certificate bytes"], @"Native diagnostics exposed certificate contents");
            if (mode<=1) {
                require(code==0 && [value[@"certificate_exported"] boolValue], @"Successful certificate operation failed");
                require(copies==1 && [[NSData dataWithContentsOfFile:output] isEqual:fixture], @"The workflow did not write the exact returned bytes");
                require([logs containsString:@"MAECopyDCRTWithError_result"] && [logs containsString:@"certificate_file_written"], @"Copy/write results are missing");
                if (mode==1) require(issues==1 && liveBeforeCallback && [logs containsString:@"MAEIssueDCRTWithCallback_result"], @"The issuance log was not visible before the real callback");
                else require(issues==0 && ![logs containsString:@"MAEIssueDCRTWithCallback_call"], @"Cached export claimed an issuance that never ran");
            } else {
                require(code==1 && ![[NSFileManager defaultManager] fileExistsAtPath:output], @"A failed operation published a certificate");
                if (mode==2) require(issues==1 && copies==0 && [value[@"error_code"] intValue]==42 && [logs containsString:@"issuance_failed"], @"The callback's actual failure code was lost");
                if (mode==3 || mode==4) require(copies==1 && [value[@"error"] isEqual:@"cached_certificate_unavailable"], @"Unavailable/invalid cached data was not handled");
                if (mode==5) require(copies==0 && [value[@"error"] isEqual:@"certificate_api_unavailable"], @"Missing copy API was not logged safely");
                if (mode==6) require(issues==0 && [value[@"error"] isEqual:@"certificate_issuance_api_unavailable"], @"Missing issuance API was not logged safely");
            }
        }
        [[NSFileManager defaultManager] removeItemAtPath:root error:NULL];
        printf("{\"passed\":true,\"certificate_cases\":7,\"checks\":%d,\"real_issuer_contacted\":false}\n",checks);
    }
    return 0;
}
