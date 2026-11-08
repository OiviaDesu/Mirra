// Exercise actual native buttons with service/launch effects replaced by a recorder.
#define main mirra_application_main
#import "../src/app/app.m"
#undef main

@interface Wizard (Checks)
- (void)render:(NSDictionary *)state;
- (void)finishReply:(NSDictionary *)reply action:(NSString *)action revision:(NSUInteger)revision;
- (void)requestPassword:(NSString *)prompt reply:(void (^)(NSString *))reply;
- (void)submitPassword:(id)sender;
- (void)cancelPassword:(id)sender;
@end
@interface MirraLogs (Checks)
- (void)copyToPasteboard:(NSPasteboard *)pasteboard;
- (BOOL)revealDirectory:(NSURL *)directory;
@end
@interface LogFolderProbe : MirraLogs
@property NSURL *directory;
@property NSURL *revealed;
@end
@implementation LogFolderProbe
- (NSURL *)logDirectoryURL { return self.directory; }
- (BOOL)revealDirectory:(NSURL *)directory { self.revealed = directory; return YES; }
@end
@interface InterfaceProbe : MirraApp
@property NSString *lastAction;
@property NSString *lastError;
@end
@implementation InterfaceProbe
- (void)run:(NSString *)action profile:(NSString *)profile { (void)profile; self.lastAction = action; }
- (void)openMirroring:(id)sender { (void)sender; self.lastAction = @"open"; }
- (void)newSetup:(id)sender { (void)sender; self.lastAction = @"setup"; }
- (void)error:(NSString *)message { self.lastError = message; }
@end
@interface PreflightProbe : MirraApp
@property NSString *requestedAction;
@property NSUInteger launches;
@end
@implementation PreflightProbe
- (void)run:(NSString *)action profile:(NSString *)profile {
    (void)profile; self.requestedAction = action;
}
- (void)launchMirroring { self.launches++; }
@end
@interface KeyboardWizard : Wizard
@property NSUInteger advances;
@end
@implementation KeyboardWizard
- (void)advance:(id)sender { (void)sender; self.advances++; }
@end
@interface PrivateClipboardView : NSTextView
@property NSPasteboard *clipboard;
@property NSUInteger copies;
@end
@implementation PrivateClipboardView
- (void)copy:(id)sender {
    (void)sender; self.copies++;
    NSString *selection = [self.string substringWithRange:self.selectedRange];
    [self.clipboard clearContents]; [self.clipboard setString:selection forType:NSPasteboardTypeString];
}
- (void)paste:(id)sender { (void)sender; [self readSelectionFromPasteboard:self.clipboard]; }
- (void)cut:(id)sender { [self copy:sender]; [self delete:sender]; }
@end

static int checks = 0;
static void pump(void) {
    [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.15]];
}
static void require(BOOL condition, NSString *message) {
    checks++; if (!condition) { fprintf(stderr, "%s\n", message.UTF8String); exit(1); }
}
static void checkPage(MirraApp *app, NSStackView *stack, BOOL fits, BOOL centered) {
    [app.window.contentView layoutSubtreeIfNeeded];
    NSScrollView *scroll = stack.enclosingScrollView; NSView *document = scroll.documentView;
    require(scroll != nil && document != nil, @"A page has no scrolling path for overflowing content");
    NSRect content = [stack convertRect:stack.bounds toView:document];
    require(fabs(NSWidth(document.bounds)-NSWidth(scroll.contentView.bounds)) < 1,
            [NSString stringWithFormat:@"Page content introduced horizontal scrolling on %@: document=%@ clip=%@ clipFrame=%@ contentSize=%@ scrollFrame=%@ page=%@ window=%@",
             app.route, NSStringFromRect(document.bounds), NSStringFromRect(scroll.contentView.bounds),
             NSStringFromRect(scroll.contentView.frame), NSStringFromSize(scroll.contentSize), NSStringFromRect(scroll.frame),
             NSStringFromRect(app.body.bounds), NSStringFromRect(app.window.contentView.bounds)]);
    require(NSContainsRect(document.bounds,content), @"Page content extends outside its scrollable document");
    if (fits) {
        require(fabs(NSHeight(document.bounds)-NSHeight(scroll.contentView.bounds)) < 1,
                @"Ordinary page content requires unnecessary scrolling");
        if (centered) require(fabs(NSMidY(content)-NSMidY(document.bounds)) < 1, @"Page content is not centered in its viewport");
    }
}
static void checkReachable(NSView *view) {
    [view scrollRectToVisible:view.bounds]; [view.window.contentView layoutSubtreeIfNeeded];
    NSScrollView *scroll = view.enclosingScrollView;
    NSRect item = [view convertRect:view.bounds toView:scroll.documentView];
    require(NSContainsRect(NSInsetRect(scroll.documentVisibleRect,-1,-1),item),
            [NSString stringWithFormat:@"Scrolling cannot bring %@ fully into view: item=%@ visible=%@",view.accessibilityLabel ?: NSStringFromClass(view.class),NSStringFromRect(item),NSStringFromRect(scroll.documentVisibleRect)]);
}
int main(void) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular]; [NSApp finishLaunching];
        PreflightProbe *connection = [[PreflightProbe alloc] init]; [connection buildWindow];
        [connection.window makeKeyAndOrderFront:nil];
        [connection openMirroring:nil];
        require([connection.requestedAction isEqual:@"preflight"] && connection.launches == 0,
                @"Connect opened Mirroring before checking the bridge");
        NSDictionary *readyReport = @{@"state":@"ready",@"actions":@[@"open_mirroring",@"stop"]};
        [connection acceptReply:@{@"ok":@YES,@"report":readyReport,@"preflight":@{@"state":@"ready"}}
                         action:@"preflight" snapshot:NO];
        require(connection.launches == 1 && connection.alerts.alert == nil,
                @"A ready bridge did not open Mirroring directly without a target/radio prompt");
        [connection acceptReply:@{@"ok":@YES,@"report":@{@"state":@"waiting_for_bridge",@"actions":@[@"refresh",@"stop"]},
            @"preflight":@{@"state":@"blocked",@"title":@"Your setup phone isn’t ready",@"message":@"Reconnect the bridge."}}
                         action:@"preflight" snapshot:NO]; pump();
        require(connection.launches == 1 && [connection.alerts.alert.messageText isEqual:@"Your setup phone isn’t ready"],
                @"An unavailable bridge launched Mirroring or lost its recovery guidance");
        [connection.window endSheet:connection.alerts.alert.window returnCode:NSAlertFirstButtonReturn]; pump();
        [connection acceptReply:@{@"ok":@YES,@"report":@{@"state":@"unknown"},@"preflight":@{@"state":@"ready"}}
                         action:@"preflight" snapshot:NO]; pump();
        require(connection.launches == 1 && connection.alerts.alert != nil,
                @"A contradictory bridge result opened Mirroring");
        [connection.window endSheet:connection.alerts.alert.window returnCode:NSAlertFirstButtonReturn]; pump();
        [connection.window orderOut:nil];
        InterfaceProbe *app = [[InterfaceProbe alloc] init]; [app buildWindow];
        require(!app.logs.expanded && !app.logs.view.hidden && app.logs.textView.selectable && !app.logs.textView.editable,
                @"Logs must start collapsed, with selectable text when opened");
        require(NSEqualSizes(app.window.contentView.bounds.size, NSMakeSize(600,570)), @"Logging enlarged the initial window");
        require(app.settingsButton.image != nil && app.settingsButton.imagePosition == NSImageOnly
                && app.settingsButton.title.length == 0 && [app.settingsButton.accessibilityLabel isEqual:@"Settings"],
                @"Settings must be an accessible icon button");
        require(app.logs.view.window == nil, @"Log controls leaked onto Home");
        for (NSMenuItem *item in NSApp.mainMenu.itemArray.firstObject.submenu.itemArray)
            require(![item.title.lowercaseString containsString:@"log"], @"Log controls remain in the main menu");
        NSArray *cases = @[
            @[@"ready", @[@"open_mirroring", @"stop"], @"Connect", @"open"],
            @[@"stopped", @[@"start", @"setup"], @"Connect", @"start"],
            @[@"stopped", @[@"install", @"setup"], @"Connect", @"install"],
            @[@"setup_required", @[@"setup"], @"Set up Mirra", @"setup"],
            @[@"unknown", @[@"refresh", @"guide"], @"Check again", @"status"]
        ];
        for (NSArray *scenario in cases) {
            app.lastAction = nil;
            [app applyReport:@{@"state": scenario[0], @"actions": scenario[1]}];
            require([app.connectButton.title isEqual:scenario[2]], @"Primary button has wrong title");
            require(app.connectButton.enabled, @"Primary button is disabled for an actionable state");
            [app.connectButton performClick:nil];
            require([app.lastAction isEqual:scenario[3]], @"Native button sent the wrong action");
        }
        [app applyReport:@{@"state": @"waiting_for_bridge", @"actions": @[@"refresh", @"stop", @"guide"]}];
        require(!app.connectButton.enabled, @"Waiting must not start another service");
        app.busy = YES; [app applyReport:@{@"state": @"ready", @"actions": @[@"open_mirroring"]}];
        require(!app.connectButton.enabled && !app.settingsButton.enabled, @"Overlapping actions were not disabled");
        app.busy = NO; [app applyReport:@{@"state": @"ready", @"actions": @[@"open_mirroring", @"stop"]}];
        [app.settingsButton performClick:nil];
        require(app.settings != nil && !app.stopButton.hidden && app.startButton.hidden, @"Settings do not match the running service");
        require([app.logs.view isDescendantOf:app.settings] && app.settingsIcon.image != nil,
                @"Settings must contain the log controls and Mirra icon");
        app.pendingConnect = YES; [app.stopButton performClick:nil];
        require([app.lastAction isEqual:@"stop"] && !app.pendingConnect, @"Stop must cancel a pending connection");
        [app.setupButton performClick:nil]; require([app.lastAction isEqual:@"setup"], @"Settings setup button is not wired");
        [app closeSettings:nil];
        app.offeredSetup = NO; app.lastAction = nil;
        NSDictionary *fresh = @{@"ok": @YES, @"report": @{@"state": @"setup_required", @"actions": @[@"setup"]}};
        [app acceptReply:fresh action:@"status" snapshot:NO];
        require([app.lastAction isEqual:@"setup"], @"First launch did not offer setup");
        app.lastAction = nil; [app acceptReply:fresh action:@"status" snapshot:NO];
        require(app.lastAction == nil, @"Periodic refresh reopened dismissed setup");
        app.pendingConnect = YES;
        [app acceptReply:@{@"ok": @YES, @"report": @{@"state": @"unknown", @"actions": @[@"refresh"]}} action:@"status" snapshot:NO];
        require(app.lastAction == nil && app.pendingConnect, @"Unknown status restarted or opened a connection");
        NSDictionary *ready = @{@"ok": @YES, @"report": @{@"state": @"ready", @"actions": @[@"open_mirroring"]}};
        [app acceptReply:ready action:@"status" snapshot:NO];
        require([app.lastAction isEqual:@"open"] && !app.pendingConnect, @"Pending Connect did not open the ready app");
        app.lastAction = nil; [app acceptReply:ready action:@"status" snapshot:NO];
        require(app.lastAction == nil, @"Readiness polling opened the app twice");
        [app.window.contentView layoutSubtreeIfNeeded];
        NSRect buttonFrame = app.connectButton.frame;
        require([app beginAction:@"status"], @"Background observation did not start");
        NSUInteger beforeStop = app.revision;
        require(!app.busy && !app.changingSupport && app.connectButton.enabled && app.settingsButton.enabled,
                @"Status polling interrupted the usable interface");
        [app.window.contentView layoutSubtreeIfNeeded];
        require(NSEqualRects(buttonFrame, app.connectButton.frame), @"Polling moved the Connect button");
        require(![app beginAction:@"status"], @"Status checks can overlap");
        [app.connectButton performClick:nil]; require([app.lastAction isEqual:@"open"], @"Connect was blocked by a status check");
        require([app beginAction:@"stop"], @"Background status blocked an explicit Stop");
        [app finishReply:ready action:@"status" revision:beforeStop];
        require(app.busy && app.changingSupport && !app.connectButton.enabled,
                @"Stale status unlocked the interface during Stop");
        NSDictionary *stopped = @{@"ok": @YES, @"report": @{@"state": @"stopped", @"actions": @[@"start"]}};
        [app finishReply:stopped action:@"stop" revision:app.revision];
        require(!app.busy && [app.report[@"state"] isEqual:@"stopped"], @"Stop did not complete");
        require([app beginAction:@"status"], @"Next observation did not start");
        beforeStop = app.revision;
        require([app beginAction:@"start"], @"Start was blocked by observation");
        [app finishReply:ready action:@"start" revision:app.revision];
        [app finishReply:stopped action:@"status" revision:beforeStop];
        require([app.report[@"state"] isEqual:@"ready"] && app.connectButton.enabled,
                @"Late status replaced the completed Start result");
        [app beginAction:@"status"];
        require([app windowShouldClose:app.window], @"Background polling prevented closing Mirra");
        [app finishReply:ready action:@"status" revision:app.revision];
        require(!app.checking && !app.busy && app.refreshButton.enabled, @"Status completion left controls blocked");
        [app beginAction:@"start"];
        require(app.window.delegate == app && ![app windowShouldClose:app.window] && app.lastError != nil,
                @"Closing the window hid an unfinished service change");
        app.busy = NO; app.changingSupport = NO;
        MirraApp *journey = [[MirraApp alloc] init]; journey.preview = YES; [journey buildWindow]; [journey applyReport:ready[@"report"]];
        [journey showSettings:nil];
        require(journey.logs.view.superview != nil && !journey.logs.expanded, @"Navigation expanded or removed the collapsed log control");
        require([journey.route isEqual:@"settings"] && journey.window.attachedSheet == nil, @"Settings must navigate in the main window");
        [journey connectionDetails:nil]; require(!journey.diagnostic.hidden && !journey.window.attachedSheet, @"Details must expand inline");
        [journey guide:nil]; require([journey.route isEqual:@"help"] && !journey.window.attachedSheet, @"Help opened a popup");
        [journey back:nil]; require([journey.route isEqual:@"settings"] && !journey.diagnostic.hidden, @"Back lost Settings context");
        [journey.window makeKeyAndOrderFront:nil];
        [journey error:@"A recoverable connection error"]; pump();
        require(journey.alerts.alert.window.sheetParent == journey.window
                && [journey.alerts.alert.informativeText isEqual:@"A recoverable connection error"],
                @"Errors must use a native sheet with the recovery message");
        NSAlert *firstAlert = journey.alerts.alert;
        [journey error:@"A recoverable connection error"];
        require(journey.alerts.alert == firstAlert, @"Duplicate failures opened overlapping alerts");
        [journey.window endSheet:firstAlert.window returnCode:NSAlertFirstButtonReturn]; pump();
        require(journey.alerts.alert == nil && !journey.window.attachedSheet, @"Dismissing an error left a duplicate queued");
        [journey back:nil]; require([journey.route isEqual:@"home"] && journey.history.count == 0, @"Back did not reach Home");
        journey.wizard = [[Wizard alloc] init]; [journey.wizard loadPreview:@{@"phase":@"new"}];
        [journey newSetup:nil]; require([journey.route isEqual:@"setup"] && !journey.window.attachedSheet, @"Setup opened a separate window");
        NSTextField *host = [journey.wizard valueForKey:@"host"]; host.stringValue = @"draft.example";
        [journey back:nil]; [journey newSetup:nil]; require([host.stringValue isEqual:@"draft.example"], @"Navigation lost the SSH draft");
        [journey applyReport:stopped[@"report"]]; require([journey.route isEqual:@"setup"] && [host.stringValue isEqual:@"draft.example"], @"Status replaced the active setup page");
        [journey.wizard setValue:@YES forKey:@"busy"]; [journey back:nil];
        require([journey.route isEqual:@"setup"], @"Back abandoned an effectful setup operation");
        [journey.wizard setValue:@NO forKey:@"busy"];
        [journey.wizard setValue:@YES forKey:@"draftEdited"];
        [journey.wizard finishReply:@{@"setup":@{@"phase":@"new",@"defaults":@{@"host":@"old.example"}}} action:@"status" revision:0];
        require([host.stringValue isEqual:@"draft.example"] && host.enabled, @"Read-only setup refresh replaced or locked the draft");
        [journey.wizard setValue:@2 forKey:@"revision"]; [journey.wizard render:@{@"phase":@"trust",@"fingerprint":@"verified value"}];
        [journey.wizard finishReply:@{@"setup":@{@"phase":@"new"}} action:@"status" revision:1];
        require([[[journey.wizard valueForKey:@"state"] objectForKey:@"phase"] isEqual:@"trust"], @"Stale setup observation replaced a completed step");
        [journey.wizard render:@{@"phase":@"authorize"}];
        NSPopUpButton *transport = [journey.wizard valueForKey:@"transport"];
        [transport selectItemAtIndex:1];
        [journey.wizard render:@{@"phase":@"new"}];
        require(![[journey.wizard valueForKey:@"portRow"] isHidden], @"USB bridge SSH port is inaccessible");
        require([[journey.wizard valueForKey:@"hostRow"] isHidden], @"USB setup requires a network address");
        [journey.wizard render:@{@"phase":@"authorize"}];
        __block NSString *credential = nil; __block BOOL cancelled = NO;
        [journey.wizard requestPassword:@"Phone SSH password" reply:^(NSString *answer) { credential = answer; }];
        NSSecureTextField *secure = [journey.wizard valueForKey:@"password"];
        secure.stringValue = @"ui-test-only";
        require(![[journey.wizard valueForKey:@"passwordForm"] isHidden] && !journey.window.attachedSheet, @"Password entry left the setup page");
        [journey.wizard submitPassword:nil];
        require([credential isEqual:@"ui-test-only"] && secure.stringValue.length == 0 && [[journey.wizard valueForKey:@"passwordForm"] isHidden], @"Submitting did not pass and clear the secure field");
        [journey.wizard requestPassword:@"Try again" reply:^(NSString *answer) { cancelled = answer == nil; }]; secure.stringValue = @"discarded";
        [journey.wizard cancelPassword:nil]; require(cancelled && secure.stringValue.length == 0, @"Cancel did not clear the secure field");
        __block NSUInteger failures = 0; __block NSString *setupError = nil;
        journey.wizard.failed = ^(NSString *message) { failures++; setupError = message; };
        [journey.wizard setValue:@NO forKey:@"previewing"];
        NSDictionary *cancelledState = @{@"phase":@"authorize",@"error":@"Access was cancelled. Try again."};
        [journey.wizard finishReply:@{@"setup":cancelledState} action:@"connect" revision:2];
        require([setupError isEqual:cancelledState[@"error"]] && [[journey.wizard valueForKey:@"primary"] isEnabled],
                @"Cancelled authorization did not route its error to the native alert handler or enable retry");
        [journey.wizard render:cancelledState]; [journey.wizard render:cancelledState];
        require(failures == 1, @"Polling repeated a dismissed setup error");
        [journey.wizard render:@{@"phase":@"authorize"}]; [journey.wizard render:cancelledState];
        require(failures == 2, @"A later failed retry did not report the same error again");
        [journey.wizard setValue:@YES forKey:@"previewing"];
        [journey showSettings:nil]; [journey.window.contentView layoutSubtreeIfNeeded];
        require(journey.currentPage.frame.size.width == journey.body.frame.size.width, @"Page left the shared window layout");
        require(NSMinY(journey.logs.view.frame) >= 0 && journey.logs.view.frame.size.height <= 24,
                @"Collapsed logging takes too much window space");
        MirraLogs *logs = journey.logs;
        NSSize compact = journey.window.frame.size;
        [logs applySnapshot:@{@"ok":@YES,@"entries":@[@{@"source":@"SSH stderr",@"level":@"debug",
            @"message":@"debug1: Authentication succeeded (publickey).",@"observed_at":NSNull.null}]}];
        require(!logs.expanded && logs.textView.string.length == 0 && NSEqualSizes(compact,journey.window.frame.size),
                @"Receiving log output expanded the window without a click");
        NSButton *show = [logs valueForKey:@"expandButton"]; [show performClick:nil];
        require(logs.expanded && [show.title isEqual:@"Hide log"] && journey.window.frame.size.width == compact.width
                && journey.window.frame.size.height == compact.height + 136, @"Show log did not expand only the log area");
        require(logs.textView.enclosingScrollView.frame.size.height == 130 && logs.textView.font.pointSize <= 11,
                @"Developer logging is too large");
        require([logs.textView.string containsString:@"Authentication succeeded (publickey)."], @"Actual process output was replaced by a status label");
        require(!logs.verbose, @"Routine framework debug output must require Verbose");
        [[logs valueForKey:@"verboseButton"] performClick:nil]; require(logs.verbose, @"Verbose control is not connected");
        [logs togglePaused:nil]; NSString *paused = logs.textView.string;
        [logs recordMessage:@"MAEIssueDCRTWithCallback_result error_code=42" source:@"SSH stderr" level:@"error"];
        require(logs.paused && [logs.textView.string isEqual:paused], @"Pause changed the visible log text");
        [logs togglePaused:nil]; require([logs.textView.string containsString:@"error_code=42"], @"Resume lost entries collected while paused");
        [show performClick:nil]; [journey back:nil];
        require(!logs.expanded && logs.view.superview != nil && NSEqualSizes(compact,journey.window.frame.size),
                @"Hide log or navigation failed to restore the compact window");
        [journey showLogs:nil];
        require(logs.expanded && !journey.busy, @"Showing logs blocked the connection controls");
        NSPasteboard *copy = [NSPasteboard pasteboardWithUniqueName]; [logs copyToPasteboard:copy];
        require([[copy stringForType:NSPasteboardTypeString] isEqual:logs.textView.string], @"Copy omitted the actual displayed log");
        [copy releaseGlobally];
        [journey back:nil];
        require(!logs.expanded && logs.view.window == nil && NSEqualSizes(compact,journey.window.frame.size),
                @"Leaving Settings left the log viewer or expanded window on another page");
        [journey error:@"Show the real log in Settings"]; pump();
        [journey.window endSheet:journey.alerts.alert.window returnCode:NSAlertSecondButtonReturn]; pump();
        require([journey.route isEqual:@"settings"] && logs.expanded && logs.view.window == journey.window,
                @"An error's Show Log action failed to navigate to Settings");
        MirraLogs *noisy = [[MirraLogs alloc] init]; [noisy toggleExpanded:nil];
        [noisy recordMessage:@"actual SSH authentication and certificate stage" source:@"SSH stderr" level:@"debug"];
        NSArray *nativeSources = @[@"Apple Mirroring", @"AVConference", @"VideoToolbox", @"Decoder"];
        for (NSUInteger i=0;i<600;i++) [noisy recordMessage:[NSString stringWithFormat:@"actual audio warning %lu",(unsigned long)i] source:nativeSources[i % nativeSources.count] level:@"warning"];
        require([noisy.textView.string containsString:@"actual SSH authentication and certificate stage"]
                && [noisy.textView.string containsString:@"actual audio warning 599"]
                && ![noisy.textView.string containsString:@"actual audio warning 0\n"], @"Native warning floods displaced SSH and certificate steps");
        NSUInteger stableLength = logs.textView.string.length;
        NSDictionary *same = @{@"state":@"ready",@"detail":@"Exact returned readiness output"};
        [logs recordReport:same]; NSUInteger once = logs.textView.string.length; [logs recordReport:same];
        require(once > stableLength && logs.textView.string.length == once, @"Unchanged status polling filled the log with duplicates");
        for (NSUInteger i=0;i<600;i++) [logs recordMessage:[NSString stringWithFormat:@"real event %lu",(unsigned long)i] source:@"Bridge" level:@"debug"];
        require(![logs.textView.string containsString:@"real event 0\n"] && [logs.textView.string containsString:@"real event 599"], @"Live history did not retain a bounded recent tail");
        require(![[MirraLogs alloc] init].expanded, @"A new launch did not restore compact logging");
        LogFolderProbe *folder = [[LogFolderProbe alloc] init];
        NSURL *temporary = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString] isDirectory:YES];
        folder.directory = [temporary URLByAppendingPathComponent:@"Mirra/logs" isDirectory:YES];
        [NSFileManager.defaultManager createDirectoryAtURL:temporary withIntermediateDirectories:NO attributes:@{NSFilePosixPermissions:@0700} error:NULL];
        NSButton *directoryButton = [folder valueForKey:@"directoryButton"];
        require(!directoryButton.hidden && directoryButton.font.pointSize <= 11, @"Open log folder must be a small control available when collapsed");
        [directoryButton performClick:nil];
        NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:folder.directory.path error:NULL];
        require([folder.revealed isEqual:folder.directory] && [attributes[NSFilePosixPermissions] unsignedShortValue] == 0700,
                @"Open log folder did not create and reveal the private log directory");
        [NSFileManager.defaultManager removeItemAtURL:temporary error:NULL];

        MirraApp *layout = [[MirraApp alloc] init]; layout.preview = YES; [layout buildWindow];
        [layout applyReport:@{@"state":@"ready",@"bridge_transport":@"ssh",@"actions":@[@"open_mirroring",@"stop"]}];
        checkPage(layout,layout.home,YES,YES);
        require(fabs(NSMidX(layout.connectButton.frame)-NSMidX(layout.home.bounds)) < 1,
                @"The primary home action is not centered");
        [layout showSettings:nil]; checkPage(layout,layout.settings,YES,YES);
        NSView *identity = layout.settingsIcon.superview;
        NSRect statusRow = [identity convertRect:identity.bounds toView:layout.settings];
        require(fabs(NSMidX(statusRow)-NSMidX(layout.settings.bounds)) < 1, @"The settings status summary is not centered");
        NSRect stop = [layout.stopButton convertRect:layout.stopButton.bounds toView:layout.settings];
        NSRect setup = [layout.setupButton convertRect:layout.setupButton.bounds toView:layout.settings];
        NSRect imported = [layout.importButton convertRect:layout.importButton.bounds toView:layout.settings];
        require(fabs(NSMinX(stop)-NSMinX(imported)) < 1 && fabs(NSWidth(stop)-NSWidth(imported)) < 1,
                @"Settings action buttons are not aligned consistently");
        require(fabs(NSMidX(setup)-NSMidX(layout.settings.bounds)) < 1,
                @"Set up Mirra must be centered in Settings");
        [layout applyReport:@{@"state":@"stopped",@"bridge_transport":@"usb",@"actions":@[@"start",@"setup"]}];
        checkPage(layout,layout.settings,YES,YES);
        [layout applyReport:@{@"state":@"helper_unavailable",@"actions":@[@"refresh",@"guide"]}];
        checkPage(layout,layout.settings,YES,YES);
        require(layout.transport.hidden, @"Settings show an empty transport label");
        [layout guide:nil]; checkPage(layout,layout.help,NO,NO); checkReachable(layout.help.arrangedSubviews.lastObject);
        [layout back:nil]; checkPage(layout,layout.settings,YES,YES);
        NSMutableString *longDetail = [NSMutableString string];
        for (NSUInteger i=0;i<30;i++) [longDetail appendFormat:@"Observed connection detail %lu: the bridge returned a long diagnostic that must remain readable and scrollable.\n",(unsigned long)i];
        [layout applyReport:@{@"state":@"ready",@"bridge_transport":@"ssh",@"detail":longDetail,@"actions":@[@"open_mirroring",@"stop"]}];
        [layout connectionDetails:nil]; checkPage(layout,layout.settings,NO,YES);
        NSScrollView *detailScroll = layout.settings.enclosingScrollView;
        require(NSHeight(detailScroll.documentView.bounds) > NSHeight(detailScroll.contentView.bounds), @"Long connection details did not grow the scrolling document");
        [detailScroll.contentView scrollToPoint:NSMakePoint(0,NSHeight(detailScroll.documentView.bounds)-NSHeight(detailScroll.contentView.bounds))];
        [detailScroll reflectScrolledClipView:detailScroll.contentView];
        require(fabs(NSMaxY(detailScroll.documentVisibleRect)-NSMaxY(detailScroll.documentView.bounds)) < 1,
                @"The last connection detail cannot be reached");
        [layout connectionDetails:nil]; checkPage(layout,layout.settings,YES,YES);
        [layout back:nil]; [layout.window makeKeyAndOrderFront:nil]; [layout error:longDetail]; pump();
        checkPage(layout,layout.home,YES,YES);
        require(layout.alerts.alert.informativeText.length <= 901, @"A long diagnostic made the native alert unbounded");
        [layout.window endSheet:layout.alerts.alert.window returnCode:NSAlertFirstButtonReturn]; pump();
        checkReachable(layout.connectButton);
        layout.wizard = [[Wizard alloc] init]; [layout.wizard loadPreview:@{@"phase":@"new"}]; [layout newSetup:nil];
        checkPage(layout,layout.wizard.view,YES,YES);
        NSButton *primary = [layout.wizard valueForKey:@"primary"];
        require(fabs(NSMidX([primary convertRect:primary.bounds toView:layout.wizard.view])-NSMidX(layout.wizard.view.bounds)) < 1,
                @"The setup action is not centered");
        NSStackView *form = [layout.wizard valueForKey:@"form"];
        for (NSString *name in @[@"host",@"port",@"account"]) {
            NSTextField *field = [layout.wizard valueForKey:name]; NSRect frame = [field convertRect:field.bounds toView:form];
            require(NSContainsRect(NSInsetRect(form.bounds,-1,-1),frame) && NSWidth(frame) >= ([name isEqual:@"port"] ? 80 : 160),
                    @"A setup field overflows or becomes too narrow");
            require(field.accessibilityLabel.length > 0, @"A setup field has no accessible label");
        }
        NSTextField *addressField = [layout.wizard valueForKey:@"host"], *portField = [layout.wizard valueForKey:@"port"];
        NSRect addressFrame = [addressField convertRect:addressField.bounds toView:form];
        NSRect portFrame = [portField convertRect:portField.bounds toView:form];
        require(fabs(NSMidY(addressFrame)-NSMidY(portFrame)) < 1 && NSMaxX(addressFrame)+12 <= NSMinX(portFrame)
                && NSWidth(addressFrame) > NSWidth(portFrame)*2,
                @"SSH address and port must share a row, with a wide address left and narrow port right");
        NSPopUpButton *usbTransport = [layout.wizard valueForKey:@"transport"]; [usbTransport selectItemAtIndex:1];
        [layout.wizard render:@{@"phase":@"new"}]; checkPage(layout,layout.wizard.view,YES,YES);
        addressField.stringValue = @"bridge.example"; portField.stringValue = @"2222";
        [usbTransport selectItemAtIndex:0]; [layout.wizard render:@{@"phase":@"new"}];
        [layout.window.contentView layoutSubtreeIfNeeded];
        addressFrame = [addressField convertRect:addressField.bounds toView:form]; portFrame = [portField convertRect:portField.bounds toView:form];
        require([addressField.stringValue isEqual:@"bridge.example"] && [portField.stringValue isEqual:@"2222"]
                && fabs(NSMidY(addressFrame)-NSMidY(portFrame)) < 1 && NSMaxX(addressFrame)+12 <= NSMinX(portFrame),
                @"Switching back from USB lost the SSH draft or side-by-side layout");
        [usbTransport selectItemAtIndex:1]; [layout.wizard render:@{@"phase":@"new"}];
        InterfaceProbe *keyboard = [[InterfaceProbe alloc] init]; [keyboard buildWindow];
        [keyboard applyReport:@{@"state":@"ready",@"actions":@[@"open_mirroring",@"stop"]}];
        NSEvent *enter = [NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint modifierFlags:0
            timestamp:0 windowNumber:keyboard.window.windowNumber context:nil characters:@"\r"
            charactersIgnoringModifiers:@"\r" isARepeat:NO keyCode:36];
        require([keyboard.window performKeyEquivalent:enter] && [keyboard.lastAction isEqual:@"open"], @"Return does not activate Connect on Home");
        keyboard.lastAction = nil;
        KeyboardWizard *keyWizard = [[KeyboardWizard alloc] init]; [keyWizard loadPreview:@{@"phase":@"new"}];
        keyboard.wizard = keyWizard; [keyWizard show]; [keyboard navigate:@"setup" push:YES];
        require([keyboard.window performKeyEquivalent:enter] && keyWizard.advances == 1 && keyboard.lastAction == nil,
                @"Return on Setup invokes the wrong page's action");
        __block NSString *submittedPassword = nil;
        [keyWizard requestPassword:@"Phone SSH password" reply:^(NSString *value) { submittedPassword = value; }];
        [[keyWizard valueForKey:@"password"] setStringValue:@"keyboard-fixture-only"];
        require([keyboard.window performKeyEquivalent:enter] && [submittedPassword isEqual:@"keyboard-fixture-only"] && keyWizard.advances == 1,
                @"Return at the password prompt does not submit only the secure form");
        PrivateClipboardView *editor = [[PrivateClipboardView alloc] initWithFrame:NSMakeRect(20,20,300,60)];
        editor.clipboard = [NSPasteboard pasteboardWithUniqueName]; editor.string = @"";
        [editor.clipboard setString:@"fixture-address.example" forType:NSPasteboardTypeString];
        [keyboard.window.contentView addSubview:editor]; [NSApp activateIgnoringOtherApps:YES];
        [keyboard.window makeKeyAndOrderFront:nil];
        NSDate *activationDeadline = [NSDate dateWithTimeIntervalSinceNow:3];
        while (NSApp.keyWindow != keyboard.window && activationDeadline.timeIntervalSinceNow > 0) {
            NSEvent *activation = [NSApp nextEventMatchingMask:NSEventMaskAny untilDate:activationDeadline inMode:NSDefaultRunLoopMode dequeue:YES];
            if (!activation) break; [NSApp sendEvent:activation];
        }
        BOOL editing = [keyboard.window makeFirstResponder:editor];
        require(editing && NSApp.keyWindow == keyboard.window,
                [NSString stringWithFormat:@"The editing shortcut fixture has no active text responder: accepted=%d active=%d policy=%ld visible=%d key=%d appKey=%@ editorWindow=%@",
                    editing,NSApp.active,(long)NSApp.activationPolicy,keyboard.window.visible,keyboard.window.keyWindow,NSApp.keyWindow,editor.window]);
        NSArray *editingKeys = @[@"v",@"a",@"c",@"x"];
        NSDictionary *editingCodes = @{@"v":@9,@"a":@0,@"c":@8,@"x":@7};
        for (NSString *key in editingKeys) {
            NSEvent *command = [NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint modifierFlags:NSEventModifierFlagCommand
                timestamp:0 windowNumber:keyboard.window.windowNumber context:nil characters:key charactersIgnoringModifiers:key isARepeat:NO keyCode:[editingCodes[key] unsignedShortValue]];
            [NSApp.mainMenu update]; [NSApp sendEvent:command];
            if ([key isEqual:@"v"]) require([editor.string isEqual:@"fixture-address.example"], @"Paste did not reach the active text control");
            if ([key isEqual:@"a"]) require(editor.selectedRange.length == editor.string.length, @"Select All did not reach the active text control");
            if ([key isEqual:@"c"]) require(editor.copies == 1 && [[editor.clipboard stringForType:NSPasteboardTypeString] isEqual:editor.string], @"Copy did not reach the active text control");
            if ([key isEqual:@"x"]) require(editor.string.length == 0 && [[editor.clipboard stringForType:NSPasteboardTypeString] isEqual:@"fixture-address.example"], @"Cut did not preserve the selected text");
        }
        [editor.clipboard releaseGlobally]; [keyboard.window orderOut:nil];
        NSPopUpButton *devices = [layout.wizard valueForKey:@"devices"];
        [devices addItemWithTitle:@"A bridge phone with a very long owner name and hardware description that must fit the USB selector"];
        [devices selectItemAtIndex:1]; [layout.window.contentView layoutSubtreeIfNeeded];
        NSRect deviceFrame = [devices convertRect:devices.bounds toView:form];
        require(NSContainsRect(NSInsetRect(form.bounds,-1,-1),deviceFrame), @"A long USB device name overflows the form");
        require(NSMaxX(deviceFrame) < NSMinX([[layout.wizard valueForKey:@"scanButton"] convertRect:[[layout.wizard valueForKey:@"scanButton"] bounds] toView:form]),
                @"The USB selector overlaps Scan");
        NSArray *phases = @[@"loading",@"contact",@"trust",@"authorize",@"deploy",@"ready",@"installed"];
        for (NSString *phase in phases) {
            [layout.wizard render:@{@"phase":phase,@"support_state":@"ready",@"fingerprint":@"SHA256:mf3lZFpfIQfLS0irTSZvU5h8sTCdfp5jKnQ2OBGLhy0"}];
            checkPage(layout,layout.wizard.view,YES,YES); checkReachable(primary);
            require(fabs(NSMidX([primary convertRect:primary.bounds toView:layout.wizard.view])-NSMidX(layout.wizard.view.bounds)) < 1,
                    @"A setup step moved its primary action away from the center");
        }
        [layout.wizard render:@{@"phase":@"contact",@"error":longDetail}]; checkPage(layout,layout.wizard.view,YES,YES);
        require([[[layout.wizard valueForKey:@"state"] objectForKey:@"error"] isEqual:longDetail]
                && [[layout.wizard valueForKey:@"primary"] isEnabled],
                @"A long setup error hides Retry or loses the complete diagnostic");
        checkReachable(primary);
        [layout.wizard render:@{@"phase":@"authorize"}];
        [layout.wizard requestPassword:@"Phone SSH password" reply:^(NSString *answer) { (void)answer; }];
        checkPage(layout,layout.wizard.view,YES,YES);
        require([primary.keyEquivalent isEqual:@""] && [[[layout.wizard valueForKey:@"authorizeButton"] keyEquivalent] isEqual:@"\r"],
                @"Return can invoke a setup action while the password prompt is open");
        checkReachable([layout.wizard valueForKey:@"password"]);
        [layout.wizard cancelPassword:nil];
        require([primary.keyEquivalent isEqual:@"\r"] && [[[layout.wizard valueForKey:@"authorizeButton"] keyEquivalent] isEqual:@""],
                @"Password entry did not restore the setup keyboard action");
        [layout.wizard render:@{@"phase":@"new"}]; checkPage(layout,layout.wizard.view,YES,YES);
        [layout showSettings:nil];
        NSRect visible = layout.window.screen.visibleFrame;
        if (NSWidth(visible) >= NSWidth(layout.window.frame) && NSHeight(visible) >= NSHeight(layout.window.frame)) {
            NSRect corner = layout.window.frame; corner.origin = NSMakePoint(NSMinX(visible)+8,NSMinY(visible)+8);
            [layout.window setFrame:corner display:NO];
            for (NSUInteger i=0;i<3;i++) {
                [layout.logs toggleExpanded:nil];
                require(NSContainsRect(NSInsetRect(visible,-1,-1),layout.window.frame), @"Showing logs puts part of the window outside the screen");
                checkPage(layout,layout.settings,YES,YES);
                [layout.logs toggleExpanded:nil];
                require(NSEqualRects(corner,layout.window.frame), @"Showing and hiding logs moves or shrinks the compact window");
            }
        }
        NSAppearance *originalAppearance = NSApp.appearance;
        NSString *suite = [@"local.mirra.interface.appearance." stringByAppendingString:NSUUID.UUID.UUIDString];
        NSUserDefaults *preferences = [[NSUserDefaults alloc] initWithSuiteName:suite];
        MirraApp *theme = [[MirraApp alloc] init]; theme.preferences = preferences; theme.preview = YES; [theme buildWindow];
        [theme showSettings:nil]; [theme.window makeKeyAndOrderFront:nil]; pump();
        require([theme.appearanceMode isEqual:@"system"] && NSApp.appearance == nil && theme.appearanceControl.selectedSegment == 0,
                @"New appearance preference must follow System");
        for (NSInteger index = 1; index <= 2; index++) {
            theme.appearanceControl.selectedSegment = index;
            [NSApp sendAction:theme.appearanceControl.action to:theme.appearanceControl.target from:theme.appearanceControl]; pump();
            NSString *mode = index == 1 ? @"light" : @"dark";
            NSString *name = index == 1 ? NSAppearanceNameAqua : NSAppearanceNameDarkAqua;
            require([NSApp.appearance.name isEqual:name] && [[preferences stringForKey:@"MirraAppearance"] isEqual:mode],
                    @"Appearance control failed to apply and save its choice");
            require([[theme.window.effectiveAppearance bestMatchFromAppearancesWithNames:@[NSAppearanceNameAqua,NSAppearanceNameDarkAqua]] isEqual:name],
                    @"The chosen appearance did not reach the actual window");
            MirraApp *reopened = [[MirraApp alloc] init]; reopened.preferences = [[NSUserDefaults alloc] initWithSuiteName:suite];
            reopened.preview = YES; [reopened buildWindow]; [reopened showSettings:nil];
            require([reopened.appearanceMode isEqual:mode] && reopened.appearanceControl.selectedSegment == index,
                    @"Reopening Mirra did not restore the saved appearance");
            [reopened.window orderOut:nil];
        }
        theme.appearanceControl.selectedSegment = 0; [theme changeAppearance:theme.appearanceControl];
        require(NSApp.appearance == nil && [[preferences stringForKey:@"MirraAppearance"] isEqual:@"system"],
                @"System failed to clear the explicit appearance override");
        [preferences setObject:@"unknown" forKey:@"MirraAppearance"];
        MirraApp *fallback = [[MirraApp alloc] init]; fallback.preferences = preferences; fallback.preview = YES; [fallback buildWindow];
        require([fallback.appearanceMode isEqual:@"system"] && NSApp.appearance == nil,
                @"An unknown appearance preference did not fall back to System");
        [preferences removePersistentDomainForName:suite]; NSApp.appearance = originalAppearance;
        printf("{\"passed\":true,\"native_ui_checks\":%d,\"external_actions_performed\":false}\n", checks);
    }
    return 0;
}
