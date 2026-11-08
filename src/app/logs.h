#import <AppKit/AppKit.h>

@interface MirraLogs : NSObject
@property(readonly) NSStackView *view;
@property(readonly) NSTextView *textView;
@property(readonly) BOOL expanded;
@property(readonly) BOOL paused;
@property(readonly) BOOL verbose;
- (void)start;
- (void)stop;
- (void)refresh;
- (void)recordReport:(NSDictionary *)report;
- (void)recordSetup:(NSDictionary *)state;
- (void)recordAction:(NSString *)action setup:(BOOL)setup;
- (void)recordMessage:(NSString *)message source:(NSString *)source level:(NSString *)level;
- (void)applySnapshot:(NSDictionary *)snapshot;
- (void)toggleExpanded:(id)sender;
- (void)togglePaused:(id)sender;
- (void)toggleVerbose:(id)sender;
- (void)copyLogs:(id)sender;
- (void)openLogFolder:(id)sender;
- (NSURL *)logDirectoryURL;
@end
