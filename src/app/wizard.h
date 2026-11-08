#import <AppKit/AppKit.h>
@interface Wizard : NSObject
@property(readonly) BOOL busy;
@property(readonly) NSStackView *view;
@property(copy) void (^changed)(void);
@property(copy) void (^finished)(void);
@property(copy) void (^logState)(NSDictionary *state);
@property(copy) void (^logAction)(NSString *action);
@property(copy) void (^failed)(NSString *message);
- (void)show;
- (void)hide;
- (void)loadPreview:(NSDictionary *)state;
@end
