// Exercise the actual platform predicate, independently of this machine's OS.
#import "../src/mirroring/profile.h"
#include <stdio.h>

int main(void) {
    @autoreleasepool {
        NSUInteger cases = 0;
        for (NSString *version in @[@"15.0", @"15.1", @"15.7.2", @"26.0", @"26.7.2"]) {
            if (!MirroringMediaHostSupported(@"x86_64", version)) return 1;
            cases++;
        }
        for (NSString *version in @[@"14.7", @"10.15", @"", @"unavailable", @"15beta", @"15.", @"15..1"]) {
            if (MirroringMediaHostSupported(@"x86_64", version)) return 1;
            cases++;
        }
        for (NSString *architecture in @[@"arm64", @"i386", @"unavailable"]) {
            if (MirroringMediaHostSupported(architecture, @"15.0")) return 1;
            cases++;
        }
        printf("Passed %lu host compatibility cases\n", (unsigned long)cases);
    }
    return 0;
}
