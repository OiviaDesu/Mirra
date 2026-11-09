#import <AppKit/AppKit.h>
#import "wizard.h"

#import "interface.h"
#import "logs.h"
#import "alerts.h"

@interface MirraApp : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property NSWindow *window;
@property NSView *body;
@property NSView *currentPage;
@property NSStackView *home;
@property NSStackView *settings;
@property NSStackView *help;
@property NSTextField *pageTitle;
@property NSButton *backButton;
@property NSString *route;
@property NSMutableArray<NSString *> *history;
@property NSTextField *diagnostic;
@property NSView *diagnosticPanel;
@property NSButton *detailsButton;
@property NSTextField *headline;
@property NSTextField *detail;
@property NSTextField *transport;
@property NSTextField *settingsStatus;
@property NSTextField *caption;
@property NSProgressIndicator *progress;
@property MirraButton *connectButton;
@property NSButton *settingsButton;
@property NSImageView *settingsIcon;
@property NSSegmentedControl *appearanceControl;
@property NSUserDefaults *preferences;
@property NSString *appearanceMode;
@property NSButton *startButton;
@property NSButton *stopButton;
@property NSButton *setupButton;
@property NSButton *importButton;
@property NSButton *refreshButton;
@property NSDictionary *report;
@property BOOL busy;
@property BOOL checking;
@property BOOL checkingBridge;
@property NSUInteger revision;
@property BOOL changingSupport;
@property BOOL pendingConnect;
@property BOOL offeredSetup;
@property NSTimer *timer;
@property BOOL preview;
@property NSString *snapshotPath;
@property Wizard *wizard;
@property MirraLogs *logs;
@property MirraAlerts *alerts;
@end

@implementation MirraApp
- (NSButton *)button:(NSString *)title action:(SEL)action { return MirraAction(title, self, action, NO); }
- (void)showAbout:(id)sender {
    (void)sender;
    NSString *version = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"";
    [NSApp orderFrontStandardAboutPanelWithOptions:@{NSAboutPanelOptionApplicationVersion:version, NSAboutPanelOptionVersion:@""}];
}
- (NSImageView *)appIconWithSize:(CGFloat)size {
    NSImage *icon = [[NSImage alloc] initWithContentsOfURL:[NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"Mirra.icns"]];
    NSImageView *view = [NSImageView imageViewWithImage:icon ?: NSApp.applicationIconImage];
    view.imageScaling = NSImageScaleProportionallyUpOrDown; view.accessibilityLabel = @"Mirra";
    [view.widthAnchor constraintEqualToConstant:size].active = YES; [view.heightAnchor constraintEqualToConstant:size].active = YES;
    return view;
}
- (void)applyAppearanceMode:(NSString *)mode {
    NSArray *modes = @[@"system",@"light",@"dark"];
    self.appearanceMode = [modes containsObject:mode] ? mode : @"system";
    NSDictionary *names = @{@"light":NSAppearanceNameAqua,@"dark":NSAppearanceNameDarkAqua};
    NSString *name = names[self.appearanceMode];
    NSApp.appearance = name ? [NSAppearance appearanceNamed:name] : nil;
    self.window.appearance = NSApp.appearance;
    self.appearanceControl.selectedSegment = [modes indexOfObject:self.appearanceMode];
}
- (void)changeAppearance:(NSSegmentedControl *)sender {
    if (sender.selectedSegment < 0 || sender.selectedSegment > 2) return;
    NSString *mode = @[@"system",@"light",@"dark"][sender.selectedSegment];
    [self.preferences setObject:mode forKey:@"MirraAppearance"];
    [self applyAppearanceMode:mode];
}
- (void)buildWindow {
    if (!self.preferences) self.preferences = NSUserDefaults.standardUserDefaults;
    [self applyAppearanceMode:[self.preferences stringForKey:@"MirraAppearance"]];
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 600, 570)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Mirra"; self.window.titleVisibility = NSWindowTitleHidden; self.window.delegate = self;
    self.window.titlebarAppearsTransparent = YES; self.window.movableByWindowBackground = YES; self.window.releasedWhenClosed = NO;
    self.window.contentMinSize = NSMakeSize(600,570);
    self.window.contentView = [[MirraSurface alloc] initWithFrame:self.window.contentView.bounds];
    NSView *content = self.window.contentView;
    NSStackView *shell = MirraColumn(18); MirraFill(shell, content, 28);
    self.history = [NSMutableArray array];
    NSView *header = [[NSView alloc] init]; [header.heightAnchor constraintEqualToConstant:36].active = YES; MirraAdd(shell, header);
    self.pageTitle = MirraLabel(@"Mirra", 18, NSFontWeightSemibold); self.pageTitle.translatesAutoresizingMaskIntoConstraints = NO; [header addSubview:self.pageTitle];
    self.backButton = [self button:@"‹  Back" action:@selector(back:)]; self.backButton.keyEquivalent = @"\033";
    self.settingsButton = MirraIconAction(@"gearshape",@"Settings",self,@selector(showSettings:));
    for (NSButton *button in @[self.backButton,self.settingsButton]) { button.translatesAutoresizingMaskIntoConstraints = NO; [header addSubview:button]; }
    [NSLayoutConstraint activateConstraints:@[[self.backButton.leadingAnchor constraintEqualToAnchor:header.leadingAnchor],
        [self.backButton.centerYAnchor constraintEqualToAnchor:header.centerYAnchor],
        [self.settingsButton.trailingAnchor constraintEqualToAnchor:header.trailingAnchor],
        [self.settingsButton.centerYAnchor constraintEqualToAnchor:header.centerYAnchor],
        [self.settingsButton.widthAnchor constraintEqualToConstant:34],
        [self.settingsButton.heightAnchor constraintEqualToConstant:34],
        [self.pageTitle.centerXAnchor constraintEqualToAnchor:header.centerXAnchor], [self.pageTitle.centerYAnchor constraintEqualToAnchor:header.centerYAnchor]]];
    self.body = [[NSView alloc] init]; MirraAdd(shell, self.body);
    self.logs = [[MirraLogs alloc] init];
    self.alerts = [[MirraAlerts alloc] initWithWindow:self.window];
    __weak MirraApp *weak = self;
    self.alerts.showLogs = ^{ [weak showLogs:nil]; };
    [self.body.bottomAnchor constraintEqualToAnchor:shell.bottomAnchor].active = YES;
    [self.logs recordMessage:@"Mirra opened. Connection and setup logs update automatically." source:@"Mirra" level:@"info"];
    self.home = MirraColumn(14); self.home.alignment = NSLayoutAttributeCenterX;
    NSImageView *art = [self appIconWithSize:100]; [self.home addArrangedSubview:art];
    [self.home setCustomSpacing:24 afterView:art];
    self.headline = MirraLabel(@"Your iPhone. On your Mac.", 27, NSFontWeightBold); self.headline.alignment = NSTextAlignmentCenter; MirraAdd(self.home, self.headline);
    self.detail = MirraLabel(@"Checking your connection…", 14, NSFontWeightRegular); self.detail.alignment = NSTextAlignmentCenter;
    self.detail.textColor = NSColor.secondaryLabelColor; MirraAdd(self.home, self.detail); [self.detail.heightAnchor constraintGreaterThanOrEqualToConstant:36].active = YES;
    [self.home setCustomSpacing:18 afterView:self.detail];
    self.connectButton = MirraAction(@"Connect", self, @selector(connect:), YES); [self.home addArrangedSubview:self.connectButton];
    [self.connectButton.widthAnchor constraintEqualToConstant:264].active = YES;
    self.connectButton.keyEquivalent = @"\r";
    self.progress = [[NSProgressIndicator alloc] init]; self.progress.style = NSProgressIndicatorStyleSpinning; self.progress.controlSize = NSControlSizeSmall;
    self.progress.displayedWhenStopped = NO; self.progress.translatesAutoresizingMaskIntoConstraints = NO; [self.connectButton addSubview:self.progress];
    [NSLayoutConstraint activateConstraints:@[[self.progress.leadingAnchor constraintEqualToAnchor:self.connectButton.leadingAnchor constant:16],
        [self.progress.centerYAnchor constraintEqualToAnchor:self.connectButton.centerYAnchor], [self.progress.widthAnchor constraintEqualToConstant:16], [self.progress.heightAnchor constraintEqualToConstant:16]]];
    self.caption = MirraLabel(@"Opens Apple’s iPhone Mirroring", 12, NSFontWeightRegular); self.caption.textColor = NSColor.secondaryLabelColor;
    self.caption.alignment = NSTextAlignmentCenter; MirraAdd(self.home, self.caption);
    [self.home setCustomSpacing:24 afterView:self.caption]; [self.home addArrangedSubview:[self button:@"Connection help" action:@selector(guide:)]];
    self.transport = MirraLabel(@"", 12, NSFontWeightRegular);
    self.refreshButton = [self button:@"Refresh" action:@selector(refresh:)];
    [self navigate:@"home" push:NO]; [self applyReport:@{}]; [self.window center];
    NSMenu *menu = [[NSMenu alloc] init]; NSMenuItem *item = [[NSMenuItem alloc] init]; [menu addItem:item];
    NSMenu *appMenu = [[NSMenu alloc] initWithTitle:@"Mirra"];
    NSMenuItem *about = [appMenu addItemWithTitle:@"About Mirra" action:@selector(showAbout:) keyEquivalent:@""]; about.target = self;
    [appMenu addItem:NSMenuItem.separatorItem];
    NSMenuItem *settings = [appMenu addItemWithTitle:@"Settings" action:@selector(showSettings:) keyEquivalent:@","]; settings.target = self;
    [appMenu addItem:NSMenuItem.separatorItem]; [appMenu addItemWithTitle:@"Quit Mirra" action:@selector(terminate:) keyEquivalent:@"q"];
    item.submenu = appMenu;
    NSMenuItem *editItem = [[NSMenuItem alloc] initWithTitle:@"Edit" action:nil keyEquivalent:@""]; [menu addItem:editItem];
    NSMenu *edit = [[NSMenu alloc] initWithTitle:@"Edit"]; editItem.submenu = edit;
    [edit addItemWithTitle:@"Undo" action:NSSelectorFromString(@"undo:") keyEquivalent:@"z"];
    NSMenuItem *redo = [edit addItemWithTitle:@"Redo" action:NSSelectorFromString(@"redo:") keyEquivalent:@"z"];
    redo.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    [edit addItem:NSMenuItem.separatorItem];
    [edit addItemWithTitle:@"Cut" action:@selector(cut:) keyEquivalent:@"x"];
    [edit addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
    [edit addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
    [edit addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
    NSApp.mainMenu = menu;
}
- (NSView *)container:(NSStackView *)stack centered:(BOOL)centered {
    return MirraPage(stack, centered);
}
- (void)navigate:(NSString *)route push:(BOOL)push {
    if (self.busy || self.wizard.busy || [self.route isEqual:route]) return;
    if (push && self.route) [self.history addObject:self.route];
    if ([self.route isEqual:@"setup"]) [self.wizard hide];
    if (![route isEqual:@"settings"] && self.logs.expanded) [self.logs toggleExpanded:nil];
    [self.window makeFirstResponder:nil];
    [self.currentPage removeFromSuperview];
    self.route = route;
    BOOL home = [route isEqual:@"home"];
    self.backButton.hidden = home; self.settingsButton.hidden = !home; self.connectButton.keyEquivalent = home ? @"\r" : @"";
    self.pageTitle.hidden = home;
    self.pageTitle.stringValue = @{@"home":@"Mirra",@"settings":@"Settings",@"help":@"Connection help",@"setup":@"Set up Mirra"}[route] ?: @"Mirra";
    NSStackView *stack = home ? self.home : ([route isEqual:@"settings"] ? self.settings : ([route isEqual:@"setup"] ? self.wizard.view : self.help));
    self.currentPage = [self container:stack centered:![route isEqual:@"help"]]; MirraFill(self.currentPage, self.body, 0);
    [self.window.contentView layoutSubtreeIfNeeded];
    if (!self.preview && !NSWorkspace.sharedWorkspace.accessibilityDisplayShouldReduceMotion) {
        self.currentPage.alphaValue = 0;
        [NSAnimationContext runAnimationGroup:^(NSAnimationContext *context) { context.duration = .16; self.currentPage.animator.alphaValue = 1; } completionHandler:nil];
    }
    NSAccessibilityPostNotification(self.pageTitle, NSAccessibilityLayoutChangedNotification);
}
- (void)back:(id)sender {
    (void)sender; if (self.busy || self.wizard.busy) return;
    NSString *route = self.history.lastObject ?: @"home"; if (self.history.count) [self.history removeLastObject]; [self navigate:route push:NO];
}
- (void)addRow:(NSString *)title detail:(NSString *)detail buttons:(NSArray *)buttons to:(NSStackView *)stack {
    NSStackView *text = MirraColumn(5); MirraAdd(text, MirraLabel(title, 14, NSFontWeightSemibold));
    NSTextField *description = MirraLabel(detail, 12, NSFontWeightRegular); description.textColor = NSColor.secondaryLabelColor; MirraAdd(text, description);
    NSStackView *row = [NSStackView stackViewWithViews:@[text]]; row.spacing = 14; row.distribution = NSStackViewDistributionFill; row.alignment = NSLayoutAttributeCenterY;
    for (NSView *button in buttons) {
        [row addArrangedSubview:button]; [button.widthAnchor constraintEqualToConstant:96].active = YES;
        [button setContentHuggingPriority:750 forOrientation:NSLayoutConstraintOrientationHorizontal];
    }
    [text setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    MirraAdd(stack, row);
    if (buttons.count) [((NSView *)buttons.lastObject).trailingAnchor constraintEqualToAnchor:row.trailingAnchor].active = YES;
}
- (void)separator:(NSStackView *)stack { NSBox *line = [[NSBox alloc] init]; line.boxType = NSBoxSeparator; MirraAdd(stack,line); }
- (NSString *)primaryAction {
    NSArray *actions = self.report[@"actions"] ?: @[];
    if ([actions containsObject:@"open_mirroring"]) return @"open";
    if ([self.report[@"state"] isEqual:@"setup_required"]) return @"setup";
    if ([actions containsObject:@"start"]) return @"start";
    if ([actions containsObject:@"install"]) return @"install";
    if ([self.report[@"state"] isEqual:@"unknown"] || [self.report[@"state"] isEqual:@"helper_unavailable"]) return @"refresh";
    if ([actions containsObject:@"guide"] && ![self.report[@"state"] isEqual:@"waiting_for_bridge"] && ![self.report[@"state"] isEqual:@"restoring"]) return @"settings";
    return @"wait";
}
- (void)applyReport:(NSDictionary *)report {
    self.report = report;
    [self.logs recordReport:report];
    self.diagnostic.stringValue = [report[@"detail"] isKindOfClass:NSString.class] ? report[@"detail"] : @"No additional details are available.";
    self.backButton.enabled = !self.busy && !self.wizard.busy;
    NSString *state = report[@"state"] ?: @"checking";
    NSDictionary *copy = @{
        @"ready": @[@"Your iPhone. On your Mac.", @"Keep your iPhone nearby and locked."],
        @"stopped": @[@"Your iPhone. On your Mac.", @"Connect to start Mirra and open iPhone Mirroring."],
        @"update_available": @[@"Update Mirra", @"Your saved connection is ready to use with this version."],
        @"setup_required": @[@"Welcome to Mirra", @"Set up your connection once. We’ll guide you through it."],
        @"checking": @[@"Getting things ready", @"Checking your connection…"],
        @"starting": @[@"Getting things ready", @"Your connection is starting. This may take a moment."],
        @"stopping": @[@"Finishing up", @"Your connection is shutting down."],
        @"restoring": @[@"Reconnecting", @"Finishing the previous session before trying again."],
        @"waiting_for_bridge": @[@"Waiting for your connection", @"Keep your setup phone online. Mirra will retry automatically."],
        @"unknown": @[@"Let’s check your connection", @"Its status isn’t available right now. Try checking again."],
        @"helper_unavailable": @[@"Let’s check your connection", @"The check couldn’t finish. Try again in a moment."]};
    NSArray *message = copy[state] ?: @[@"Your connection needs attention", @"Open Settings to review your connection."];
    self.headline.stringValue = message[0]; self.detail.stringValue = message[1];
    NSString *transport = report[@"bridge_transport"];
    self.transport.stringValue = [transport isEqual:@"ssh"] ? @"Wi-Fi" : ([transport isEqual:@"usb"] ? @"USB" : @"");
    self.transport.hidden = !self.transport.stringValue.length;
    self.transport.textColor = NSColor.secondaryLabelColor;
    NSString *action = [self primaryAction];
    self.caption.hidden = ![@[@"open", @"start", @"install"] containsObject:action];
    NSDictionary *titles = @{@"open": @"Connect", @"start": @"Connect", @"install": @"Connect", @"setup": @"Set up Mirra",
                              @"refresh": @"Check again", @"settings": @"Open Settings", @"wait": @"Connecting…"};
    self.connectButton.title = [state isEqual:@"checking"] ? @"Checking…" : titles[action];
    if ([state isEqual:@"update_available"]) self.connectButton.title = @"Update and connect";
    if (self.busy) self.connectButton.title = self.checkingBridge ? @"Checking bridge…" : @"Please wait…";
    self.connectButton.enabled = !self.busy && ![action isEqual:@"wait"];
    [self.connectButton setNeedsDisplay:YES];
    self.settingsButton.enabled = !self.busy; self.refreshButton.enabled = !self.busy && !self.checking;
    NSArray *actions = [report[@"actions"] isKindOfClass:NSArray.class] ? report[@"actions"] : @[];
    self.startButton.hidden = !([actions containsObject:@"start"] || [actions containsObject:@"install"]);
    self.stopButton.hidden = ![actions containsObject:@"stop"];
    for (NSButton *button in @[self.startButton ?: self.settingsButton, self.stopButton ?: self.settingsButton,
                              self.setupButton ?: self.settingsButton, self.importButton ?: self.settingsButton]) button.enabled = !self.busy;
    NSDictionary *statusTitles = @{@"ready": @"Ready", @"stopped": @"Stopped", @"setup_required": @"Not set up",
        @"update_available": @"Update available",
        @"checking": @"Checking…", @"starting": @"Starting…", @"stopping": @"Stopping…",
        @"waiting_for_bridge": @"Waiting…", @"restoring": @"Reconnecting…", @"recovering": @"Reconnecting…",
        @"unknown": @"Status unavailable", @"helper_unavailable": @"Status unavailable"};
    self.settingsStatus.stringValue = statusTitles[state] ?: @"Needs attention";
    if (self.busy || [action isEqual:@"wait"]) [self.progress startAnimation:nil]; else [self.progress stopAnimation:nil];
    if (self.snapshotPath && report[@"state"]) [self saveSnapshotAndQuit];
}
- (void)showSettings:(id)sender {
    (void)sender; if (self.busy || self.wizard.busy) return;
    if (!self.settings) {
        self.settings = MirraColumn(12); self.settings.alignment = NSLayoutAttributeCenterX;
        self.settingsStatus = MirraLabel(@"", 18, NSFontWeightSemibold);
        NSStackView *status = [NSStackView stackViewWithViews:@[self.settingsStatus,self.transport,self.refreshButton]]; status.spacing = 12;
        status.alignment = NSLayoutAttributeCenterY;
        NSStackView *summary = MirraColumn(5); summary.alignment = NSLayoutAttributeLeading;
        [summary addArrangedSubview:status];
        id release = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
        NSString *versionText = [release isKindOfClass:NSString.class]
            ? [@"Mirra " stringByAppendingString:release] : @"Mirra · Version unavailable";
        NSTextField *version = MirraLabel(versionText,11,NSFontWeightRegular); version.textColor = NSColor.secondaryLabelColor;
        version.toolTip = @"Version of this installed app."; [summary addArrangedSubview:version];
        self.settingsIcon = [self appIconWithSize:48];
        NSStackView *identity = [NSStackView stackViewWithViews:@[self.settingsIcon,summary]];
        identity.spacing = 12; identity.alignment = NSLayoutAttributeCenterY; [self.settings addArrangedSubview:identity];
        self.appearanceControl = [NSSegmentedControl segmentedControlWithLabels:@[@"System",@"Light",@"Dark"]
            trackingMode:NSSegmentSwitchTrackingSelectOne target:self action:@selector(changeAppearance:)];
        self.appearanceControl.accessibilityLabel = @"Appearance";
        self.appearanceControl.toolTip = @"System follows your Mac. Light and Dark apply only to Mirra.";
        for (NSInteger index = 0; index < 3; index++) [self.appearanceControl setWidth:70 forSegment:index];
        [self applyAppearanceMode:self.appearanceMode];
        NSStackView *appearance = [NSStackView stackViewWithViews:@[MirraLabel(@"Appearance",13,NSFontWeightSemibold),self.appearanceControl]];
        appearance.spacing = 18; appearance.alignment = NSLayoutAttributeCenterY; [self.settings addArrangedSubview:appearance];
        self.startButton = [self button:@"Start" action:@selector(start:)]; self.stopButton = [self button:@"Stop" action:@selector(stop:)];
        NSStackView *connection = MirraColumn(12);
        [self addRow:@"Background connection" detail:@"Available at login and when Mirra is closed." buttons:@[self.startButton,self.stopButton] to:connection];
        MirraAdd(self.settings,MirraCard(connection,14));
        NSStackView *setup = MirraColumn(10); setup.alignment = NSLayoutAttributeCenterX;
        NSStackView *prepare = MirraColumn(6); prepare.alignment = NSLayoutAttributeCenterX;
        NSTextField *setupTitle = MirraLabel(@"Setup phone",14,NSFontWeightSemibold); setupTitle.alignment = NSTextAlignmentCenter;
        NSTextField *setupNote = MirraLabel(@"Prepare a jailbroken phone for your connection.",12,NSFontWeightRegular);
        setupNote.textColor = NSColor.secondaryLabelColor; setupNote.alignment = NSTextAlignmentCenter;
        MirraAdd(prepare,setupTitle); MirraAdd(prepare,setupNote);
        self.setupButton = [self button:@"Set up Mirra" action:@selector(newSetup:)];
        [self.setupButton.widthAnchor constraintEqualToConstant:160].active = YES; [prepare addArrangedSubview:self.setupButton];
        MirraAdd(setup,prepare);
        [self separator:setup];
        self.importButton = [self button:@"Import…" action:@selector(chooseProfile:)];
        [self addRow:@"Saved setup" detail:@"Restore an existing ready.json profile." buttons:@[self.importButton] to:setup];
        MirraAdd(self.settings,MirraCard(setup,14));
        self.detailsButton = [self button:@"Show details" action:@selector(connectionDetails:)];
        NSStackView *links = [NSStackView stackViewWithViews:@[self.detailsButton,[self button:@"Connection help" action:@selector(guide:)]]]; links.spacing = 12; [self.settings addArrangedSubview:links];
        self.diagnostic = MirraLabel(@"", 12, NSFontWeightRegular); self.diagnostic.textColor = NSColor.secondaryLabelColor;
        self.diagnostic.lineBreakMode = NSLineBreakByCharWrapping;
        NSStackView *details = MirraColumn(8); MirraAdd(details,self.diagnostic);
        self.diagnosticPanel = MirraCard(details,14); MirraAdd(self.settings,self.diagnosticPanel);
        self.diagnostic.hidden = YES; self.diagnosticPanel.hidden = YES;
        MirraAdd(self.settings,self.logs.view);
    }
    [self applyReport:self.report]; [self navigate:@"settings" push:YES];
}
- (void)connectionDetails:(id)sender {
    (void)sender; self.diagnostic.hidden = !self.diagnostic.hidden; self.detailsButton.title = self.diagnostic.hidden ? @"Show details" : @"Hide details";
    self.diagnosticPanel.hidden = self.diagnostic.hidden;
    [self.window.contentView layoutSubtreeIfNeeded];
    if (!self.diagnostic.hidden) {
        NSScrollView *scroll = self.diagnostic.enclosingScrollView;
        NSRect details = [self.diagnostic convertRect:self.diagnostic.bounds toView:scroll.documentView];
        [scroll.contentView scrollToPoint:NSMakePoint(0,MAX(0,NSMinY(details)-14))];
        [scroll reflectScrolledClipView:scroll.contentView];
    }
}
- (void)closeSettings:(id)sender { [self back:sender]; }
- (BOOL)windowShouldClose:(NSWindow *)window {
    (void)window;
    if (self.changingSupport || self.wizard.busy) {
        [self error:@"Setup is still running. Keep Mirra open until it finishes."]; return NO;
    }
    return YES;
}
- (void)connect:(id)sender {
    (void)sender; if (self.busy || self.preview) return;
    NSString *action = [self primaryAction];
    if ([action isEqual:@"setup"]) [self newSetup:nil];
    else if ([action isEqual:@"open"]) [self openMirroring:nil];
    else if ([action isEqual:@"start"] || [action isEqual:@"install"]) { self.pendingConnect = YES; [self run:action profile:nil]; }
    else if ([action isEqual:@"settings"]) [self showSettings:nil];
    else if ([action isEqual:@"refresh"]) [self refresh:nil];
}
- (void)saveSnapshotAndQuit {
    NSString *destination = self.snapshotPath; self.snapshotPath = nil;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(.4 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self.window.contentView layoutSubtreeIfNeeded]; [self.window displayIfNeeded];
        NSView *view = self.window.contentView;
        [view layoutSubtreeIfNeeded]; NSBitmapImageRep *bitmap = [view bitmapImageRepForCachingDisplayInRect:view.bounds];
        [view cacheDisplayInRect:view.bounds toBitmapImageRep:bitmap];
        [[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:destination atomically:YES];
        NSData *data = [NSJSONSerialization dataWithJSONObject:self.report options:NSJSONWritingPrettyPrinted error:NULL];
        [data writeToFile:[destination stringByAppendingString:@".json"] atomically:YES];
        [NSApp terminate:nil];
    });
}
- (void)error:(NSString *)message {
    [self.logs recordMessage:message source:@"Mirra" level:@"error"];
    [self.alerts presentError:message title:@"Couldn’t complete the action"];
}
- (void)acceptReply:(NSDictionary *)reply action:(NSString *)action snapshot:(BOOL)snapshot {
    self.busy = NO; self.changingSupport = NO; self.checkingBridge = NO;
    if ([reply isKindOfClass:NSDictionary.class] && [reply[@"report"] isKindOfClass:NSDictionary.class]) {
        [self applyReport:reply[@"report"]];
        if (![reply[@"ok"] boolValue]) { self.pendingConnect = NO; [self error:reply[@"message"] ?: @"Check the connection before trying again."]; }
        else if ([action isEqual:@"preflight"]) {
            NSDictionary *checked = [reply[@"preflight"] isKindOfClass:NSDictionary.class] ? reply[@"preflight"] : @{};
            NSString *state = checked[@"state"];
            if ([state isEqual:@"blocked"]) {
                NSString *message = [checked[@"message"] isKindOfClass:NSString.class] ? checked[@"message"] : @"Check your setup phone and try again.";
                [self.logs recordMessage:message source:@"Connection check" level:@"warning"];
                [self.alerts presentError:message title:checked[@"title"]];
            } else if ([state isEqual:@"ready"] && [self.report[@"state"] isEqual:@"ready"]) {
                if (self.window.visible) [self launchMirroring];
            } else [self error:@"The connection check did not return a usable result. Check again before opening iPhone Mirroring."];
        }
        else if (self.pendingConnect && [self.report[@"state"] isEqual:@"ready"]) { self.pendingConnect = NO; [self openMirroring:nil]; }
        else if (!snapshot && !self.offeredSetup && [self.report[@"state"] isEqual:@"setup_required"] && [self.route isEqual:@"home"]) {
            self.offeredSetup = YES; [self newSetup:nil];
        }
    } else {
        self.pendingConnect = NO;
        [self applyReport:@{@"state": @"helper_unavailable", @"title": @"Let’s check the connection", @"detail": @"Mirra’s helper could not finish. Reopen a complete copy of Mirra, then check again.", @"actions": @[@"refresh", @"guide"]}];
        if (![action isEqual:@"status"]) [self error:@"The helper did not return a result. Check the connection before repeating the action."];
    }
}
- (BOOL)beginAction:(NSString *)action {
    if (self.busy || self.preview || self.wizard.busy) return NO;
    if ([action isEqual:@"status"]) {
        if (self.checking) return NO;
        self.checking = YES;
        // A background observation must never lock Connect or Settings.
        self.refreshButton.enabled = NO;
    } else {
        self.revision++;
        self.busy = YES; self.checkingBridge = [action isEqual:@"preflight"];
        self.changingSupport = !self.checkingBridge;
        [self.logs recordAction:action setup:NO];
        [self applyReport:self.report];
    }
    return YES;
}
- (void)finishReply:(NSDictionary *)reply action:(NSString *)action revision:(NSUInteger)revision {
    if ([action isEqual:@"status"]) {
        self.checking = NO;
        self.refreshButton.enabled = !self.busy;
        // A result collected before Start, Stop or setup cannot replace their state.
        if (revision != self.revision || self.busy || self.wizard.busy) return;
    }
    [self acceptReply:reply action:action snapshot:self.snapshotPath != nil];
}
- (void)run:(NSString *)action profile:(NSString *)profile {
    if (![self beginAction:action]) return;
    NSUInteger revision = self.revision;
    NSString *python = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:[NSBundle.mainBundle objectForInfoDictionaryKey:@"SupportPython"]].path;
    NSURL *script = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"Support/scripts/observe.py"];
    NSMutableArray *arguments = [NSMutableArray arrayWithObjects:@"-E", @"-s", @"-B", script.path, @"control", action, nil];
    if (profile) [arguments addObjectsFromArray:@[@"--profile", profile]];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSTask *task = [[NSTask alloc] init]; task.executableURL = [NSURL fileURLWithPath:python]; task.arguments = arguments;
        NSPipe *output = [NSPipe pipe]; task.standardOutput = output; task.standardError = NSFileHandle.fileHandleWithNullDevice;
        NSError *error = nil; BOOL launched = [task launchAndReturnError:&error];
        NSData *data = launched ? [output.fileHandleForReading readDataToEndOfFile] : nil; if (launched) [task waitUntilExit];
        NSDictionary *reply = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            [self finishReply:reply action:action revision:revision];
        });
    });
}
- (void)refresh:(id)sender { (void)sender; [self run:@"status" profile:nil]; }
- (void)poll:(id)sender {
    (void)sender;
    if ([self.route isEqual:@"setup"] || self.window.attachedSheet || (!NSApp.active && !self.pendingConnect)) return;
    [self refresh:nil];
}
- (void)applicationDidBecomeActive:(NSNotification *)notification {
    (void)notification; if (self.window && ![self.route isEqual:@"setup"] && !self.window.attachedSheet) [self refresh:nil];
}
- (void)start:(id)sender { (void)sender; [self run:[self.report[@"actions"] containsObject:@"install"] ? @"install" : @"start" profile:nil]; }
- (void)stop:(id)sender { (void)sender; self.pendingConnect = NO; [self run:@"stop" profile:nil]; }
- (void)openMirroring:(id)sender {
    (void)sender;
    [self run:@"preflight" profile:nil];
}
- (void)launchMirroring {
    [self.logs recordMessage:@"Opening Apple’s iPhone Mirroring. Complete discovery and authentication in its window." source:@"Mirroring" level:@"info"];
    [NSWorkspace.sharedWorkspace openApplicationAtURL:[NSURL fileURLWithPath:@"/System/Applications/iPhone Mirroring.app"] configuration:[NSWorkspaceOpenConfiguration configuration]
        completionHandler:^(NSRunningApplication *app, NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{
                if (error) {
                    [self.logs recordMessage:[NSString stringWithFormat:@"App launch failed (%@, %ld).", error.domain, (long)error.code] source:@"Mirroring" level:@"error"];
                    [self error:@"Apple’s iPhone Mirroring could not open. Check the connection and try again."];
                } else [self.logs recordMessage:[NSString stringWithFormat:@"iPhone Mirroring opened (process %d).", app.processIdentifier] source:@"Mirroring" level:@"info"];
            });
        }];
}
- (void)chooseProfile:(id)sender {
    (void)sender; if (self.busy || self.wizard.busy) return; self.revision++;
    NSOpenPanel *panel = [NSOpenPanel openPanel]; panel.canChooseDirectories = NO; panel.allowsMultipleSelection = NO;
    panel.message = @"Choose the ready.json file from a previous Mirra setup. Stop Mirra before changing the bridge.";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) { if (response == NSModalResponseOK) [self run:@"install" profile:panel.URL.path]; }];
}
- (void)newSetup:(id)sender {
    (void)sender; if (self.busy || self.wizard.busy) return; self.revision++;
    if (!self.wizard) {
        self.wizard = [[Wizard alloc] init]; __weak MirraApp *weak = self;
        self.wizard.logState = ^(NSDictionary *state) { [weak.logs recordSetup:state]; };
        self.wizard.logAction = ^(NSString *action) { [weak.logs recordAction:action setup:YES]; };
        self.wizard.failed = ^(NSString *message) {
            [weak.logs recordMessage:message source:@"Setup" level:@"error"];
            [weak.alerts presentError:message title:@"Couldn’t finish this setup step"];
        };
        self.wizard.changed = ^{ weak.backButton.enabled = !weak.wizard.busy; };
        self.wizard.finished = ^{ [weak.history removeAllObjects]; [weak navigate:@"home" push:NO]; [weak refresh:nil]; [weak openMirroring:nil]; };
    }
    [self.wizard show]; [self navigate:@"setup" push:YES];
}
- (void)guide:(id)sender {
    (void)sender; if (!self.help) {
        self.help = MirraColumn(22);
        MirraAdd(self.help, MirraLabel(@"Ready when you are", 24, NSFontWeightBold));
        NSArray *rows = @[
            @[@"1. Prepare once",@"Use a separate jailbroken phone with SSH and ldid installed. Set up Mirra to verify that phone and create its certificate automatically."],
            @[@"2. Keep both phones available",@"Keep the setup phone reachable. The iPhone you mirror should be locked and nearby, with Wi-Fi and Bluetooth on. Use the same Apple Account on your Mac and that iPhone."],
            @[@"3. Connect",@"Choose Connect on Mirra’s home screen. Apple’s iPhone Mirroring opens for authentication and your live screen."],
            @[@"If it takes too long",@"Check that the setup phone is online, then use Refresh in Settings. If Apple’s app times out, let that attempt finish before trying again."]];
        for (NSArray *row in rows) [self addRow:row[0] detail:row[1] buttons:@[] to:self.help];
        NSTextField *scope = MirraLabel(@"Requires Intel macOS 15 or later with compatible system interfaces. Support is experimental and depends on your configuration.",12,NSFontWeightRegular); scope.textColor = NSColor.secondaryLabelColor; MirraAdd(self.help,scope);
    }
    [self navigate:@"help" push:YES];
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification; NSArray *arguments = NSProcessInfo.processInfo.arguments;
    [self buildWindow];
    if ([arguments containsObject:@"--light"]) [self applyAppearanceMode:@"light"];
    if ([arguments containsObject:@"--dark"]) [self applyAppearanceMode:@"dark"];
    NSUInteger wizardPreview = [arguments indexOfObject:@"--preview-setup"];
    if (wizardPreview != NSNotFound && wizardPreview + 2 < arguments.count) {
        self.preview = YES;
        NSData *data = [NSData dataWithContentsOfFile:arguments[wizardPreview + 1]];
        NSDictionary *state = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
        if (![state isKindOfClass:NSDictionary.class]) { [NSApp terminate:nil]; return; }
        self.wizard = [[Wizard alloc] init]; [self.wizard loadPreview:state];
        [self navigate:@"setup" push:YES]; [self.window makeKeyAndOrderFront:nil];
        if ([state[@"logs"] isKindOfClass:NSDictionary.class]) [self.logs applySnapshot:state[@"logs"]];
        if ([arguments containsObject:@"--show-logs"]) [self showLogs:nil];
        self.snapshotPath = arguments[wizardPreview + 2]; [self saveSnapshotAndQuit]; return;
    }
    NSUInteger snapshot = [arguments indexOfObject:@"--snapshot-status"];
    if (snapshot != NSNotFound && snapshot + 1 < arguments.count) self.snapshotPath = arguments[snapshot + 1];
    NSUInteger preview = [arguments indexOfObject:@"--preview"];
    if (preview != NSNotFound && preview + 2 < arguments.count) {
        self.preview = YES; NSData *data = [NSData dataWithContentsOfFile:arguments[preview + 1]];
        NSDictionary *value = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
        if (![value isKindOfClass:NSDictionary.class]) { [NSApp terminate:nil]; return; }
        [self.window makeKeyAndOrderFront:nil]; self.snapshotPath = arguments[preview + 2]; [self applyReport:value];
        if ([value[@"logs"] isKindOfClass:NSDictionary.class]) [self.logs applySnapshot:value[@"logs"]];
        if ([arguments containsObject:@"--settings"]) [self showSettings:nil];
        if ([arguments containsObject:@"--show-logs"]) [self showLogs:nil];
        if ([arguments containsObject:@"--show-details"] && [self.route isEqual:@"settings"]) [self connectionDetails:nil];
        if ([arguments containsObject:@"--help"]) [self guide:nil]; return;
    }
    [self.window makeKeyAndOrderFront:nil];
    if ([arguments containsObject:@"--settings"]) [self showSettings:nil];
    [NSApp activateIgnoringOtherApps:YES]; [self.logs start]; [self refresh:nil];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:15 target:self selector:@selector(poll:) userInfo:nil repeats:YES];
    self.timer.tolerance = 2;
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)application { (void)application; return YES; }
- (void)showLogs:(id)sender {
    (void)sender; [self showSettings:nil];
    if ([self.route isEqual:@"settings"] && !self.logs.expanded) {
        [self.logs toggleExpanded:nil]; [self.logs.view scrollRectToVisible:self.logs.view.bounds];
    }
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)application {
    (void)application;
    if (self.preview) return NSTerminateNow;
    if (self.changingSupport || self.wizard.busy) { [self error:@"Setup is still running. Keep Mirra open until it finishes."]; return NSTerminateCancel; }
    return NSTerminateNow;
}
- (void)applicationWillTerminate:(NSNotification *)notification { (void)notification; [self.logs stop]; }
@end

int main(int argc, const char *argv[]) {
    (void)argc; (void)argv;
    @autoreleasepool { NSApplication *application = NSApplication.sharedApplication;
        MirraApp *delegate = [[MirraApp alloc] init]; application.delegate = delegate;
        [application setActivationPolicy:NSApplicationActivationPolicyRegular]; [application run]; }
    return 0;
}
