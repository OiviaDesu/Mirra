#import "alerts.h"

@interface MirraAlertRequest : NSObject
@property NSString *title;
@property NSString *message;
@property NSString *continueTitle;
@property(copy) void (^completion)(BOOL);
@end
@implementation MirraAlertRequest
@end

@interface MirraAlerts ()
@property(weak) NSWindow *window;
@property(readwrite) NSAlert *alert;
@property MirraAlertRequest *current;
@property NSMutableArray<MirraAlertRequest *> *pending;
@end

@implementation MirraAlerts
- (instancetype)initWithWindow:(NSWindow *)window {
    if ((self = [super init])) {
        _window = window; _pending = [NSMutableArray array];
        for (NSString *name in @[NSWindowDidEndSheetNotification, NSWindowDidBecomeKeyNotification])
            [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(windowChanged:) name:name object:window];
    }
    return self;
}
- (void)dealloc { [NSNotificationCenter.defaultCenter removeObserver:self]; }
- (void)windowChanged:(NSNotification *)notification {
    (void)notification;
    dispatch_async(dispatch_get_main_queue(), ^{ [self presentNext]; });
}
- (NSString *)bounded:(NSString *)text limit:(NSUInteger)limit {
    NSString *value = [text isKindOfClass:NSString.class] ? text : @"";
    if (value.length <= limit) return value;
    NSRange range = [value rangeOfComposedCharacterSequencesForRange:NSMakeRange(0, limit)];
    return [[value substringWithRange:range] stringByAppendingString:@"…"];
}
- (void)enqueue:(MirraAlertRequest *)request {
    // Polling or two simultaneous failures must not create duplicate sheets.
    for (MirraAlertRequest *existing in self.current ? [self.pending arrayByAddingObject:self.current] : self.pending) {
        if (!request.completion && !existing.completion && [existing.title isEqual:request.title]
            && [existing.message isEqual:request.message]) return;
    }
    [self.pending addObject:request]; [self presentNext];
}
- (void)presentError:(NSString *)message title:(NSString *)title {
    MirraAlertRequest *request = [[MirraAlertRequest alloc] init];
    request.title = [self bounded:title limit:80]; request.message = [self bounded:message limit:900];
    [self enqueue:request];
}
- (void)confirm:(NSString *)message title:(NSString *)title
    continueTitle:(NSString *)continueTitle completion:(void (^)(BOOL))completion {
    MirraAlertRequest *request = [[MirraAlertRequest alloc] init];
    request.title = [self bounded:title limit:80]; request.message = [self bounded:message limit:900];
    request.continueTitle = continueTitle; request.completion = completion;
    [self enqueue:request];
}
- (void)presentNext {
    if (self.alert || self.window.attachedSheet || !self.window.visible || !self.pending.count) return;
    self.current = self.pending.firstObject; [self.pending removeObjectAtIndex:0];
    MirraAlertRequest *request = self.current;
    NSAlert *alert = [[NSAlert alloc] init]; self.alert = alert;
    alert.alertStyle = request.completion ? NSAlertStyleInformational : NSAlertStyleWarning;
    alert.messageText = request.title.length ? request.title : @"Mirra needs your attention";
    alert.informativeText = request.message;
    [alert addButtonWithTitle:request.continueTitle ?: @"OK"];
    if (request.completion) [alert addButtonWithTitle:@"Cancel"];
    else if (self.showLogs) [alert addButtonWithTitle:@"Show Log"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        self.alert = nil; self.current = nil;
        if (request.completion) request.completion(response == NSAlertFirstButtonReturn);
        else if (response == NSAlertSecondButtonReturn && self.showLogs) self.showLogs();
        [self presentNext];
    }];
}
@end
