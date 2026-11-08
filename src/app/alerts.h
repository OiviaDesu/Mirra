#import <AppKit/AppKit.h>

@interface MirraAlerts : NSObject
@property(copy) void (^showLogs)(void);
@property(readonly) NSAlert *alert;
- (instancetype)initWithWindow:(NSWindow *)window;
- (void)presentError:(NSString *)message title:(NSString *)title;
- (void)confirm:(NSString *)message title:(NSString *)title
    continueTitle:(NSString *)continueTitle completion:(void (^)(BOOL accepted))completion;
@end
