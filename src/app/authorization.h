#import <Foundation/Foundation.h>
// Credentials stay between the native UI and OpenSSH; Python receives no password.
@interface Authorization : NSObject
@property(readonly) NSDictionary<NSString *, NSString *> *environment;
- (instancetype)initWithPrompt:(void (^)(NSString *, void (^)(NSString *)))prompt;
- (void)invalidate;
@end
