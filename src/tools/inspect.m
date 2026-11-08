#import "../mirroring/profile.h"
#include <stdio.h>

int main(void) {
    @autoreleasepool {
        NSDictionary *report = MirroringMediaCompatibilityReport();
        NSData *json = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:NULL];
        if (!json) return 2;
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
        return [report[@"compatible"] boolValue] ? 0 : 1;
    }
}
