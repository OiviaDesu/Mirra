#import "wizard.h"
#import "interface.h"
#import "authorization.h"

@interface Wizard () <NSTextFieldDelegate>
@property(readwrite) NSStackView *view;
@property BOOL active;
@property BOOL checking;
@property BOOL scanning;
@property BOOL loadedDefaults;
@property BOOL draftEdited;
@property NSUInteger revision;
@property dispatch_queue_t queue;
@property Authorization *authorization;
@property NSStackView *passwordForm;
@property NSStackView *actionRow;
@property NSSecureTextField *password;
@property NSTextField *passwordPrompt;
@property(copy) void (^passwordReply)(NSString *);
@property NSTextField *steps;
@property NSButton *scanButton;
@property(readwrite) BOOL busy;
@property BOOL previewing;
@property NSDictionary *state;
@property NSTextField *heading;
@property NSTextField *detail;
@property NSTextField *fingerprint;
@property NSString *lastError;
@property NSStackView *form;
@property NSView *formPanel;
@property NSStackView *hostRow;
@property NSStackView *portRow;
@property NSStackView *usbRow;
@property NSPopUpButton *transport;
@property NSPopUpButton *devices;
@property NSTextField *host;
@property NSTextField *port;
@property NSTextField *account;
@property NSButton *primary;
@property NSButton *authorizeButton;
@property NSButton *again;

@property NSProgressIndicator *progress;
@property NSTimer *timer;
@end

@implementation Wizard
- (NSStackView *)row:(NSString *)title control:(NSView *)control {
    NSTextField *caption = MirraLabel(title, 13, NSFontWeightMedium);
    [caption.widthAnchor constraintEqualToConstant:96].active = YES;
    NSStackView *row = [NSStackView stackViewWithViews:@[caption, control]];
    row.spacing = 12; row.alignment = NSLayoutAttributeCenterY; row.distribution = NSStackViewDistributionFill;
    [control setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    [control setContentCompressionResistancePriority:250 forOrientation:NSLayoutConstraintOrientationHorizontal];
    control.accessibilityLabel = title;
    [control.widthAnchor constraintGreaterThanOrEqualToConstant:160].active = YES;
    [control.heightAnchor constraintGreaterThanOrEqualToConstant:30].active = YES;
    return row;
}
- (void)build {
    if (self.view) return;
    self.queue = dispatch_queue_create("local.mirra.setup", DISPATCH_QUEUE_SERIAL);
    self.view = MirraColumn(12); self.view.alignment = NSLayoutAttributeCenterX; NSStackView *stack = self.view;
    self.steps = MirraLabel(@"PHONE  /  VERIFY  /  ACCESS  /  PREPARE  /  FINISH", 10, NSFontWeightSemibold);
    self.steps.textColor = NSColor.secondaryLabelColor; self.steps.alignment = NSTextAlignmentCenter; MirraAdd(stack, self.steps);
    self.heading = MirraLabel(@"Connect your setup phone", 24, NSFontWeightBold);
    self.detail = MirraLabel(@"", 13, NSFontWeightRegular); self.detail.textColor = NSColor.secondaryLabelColor;
    self.heading.alignment = NSTextAlignmentCenter; self.detail.alignment = NSTextAlignmentCenter;
    MirraAdd(stack, self.heading); MirraAdd(stack, self.detail);
    self.form = MirraColumn(10);
    self.transport = [[NSPopUpButton alloc] init]; [self.transport addItemsWithTitles:@[@"Wi-Fi (SSH)", @"USB (SSH)"]];
    self.transport.target = self; self.transport.action = @selector(transportChanged:);
    MirraAdd(self.form, [self row:@"Connect using" control:self.transport]);
    self.host = [NSTextField textFieldWithString:@""]; self.host.placeholderString = @"Phone IP address or hostname";
    self.port = [NSTextField textFieldWithString:@"22"];
    self.hostRow = MirraColumn(6); self.portRow = MirraColumn(6);
    MirraAdd(self.hostRow,MirraLabel(@"SSH address",13,NSFontWeightMedium)); MirraAdd(self.hostRow,self.host);
    MirraAdd(self.portRow,MirraLabel(@"Port",13,NSFontWeightMedium)); MirraAdd(self.portRow,self.port);
    self.host.accessibilityLabel = @"SSH address"; self.port.accessibilityLabel = @"SSH port";
    [self.host.widthAnchor constraintGreaterThanOrEqualToConstant:240].active = YES;
    [self.portRow.widthAnchor constraintEqualToConstant:88].active = YES;
    for (NSView *field in @[self.host,self.port]) [field.heightAnchor constraintEqualToConstant:30].active = YES;
    [self.hostRow setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSStackView *address = [NSStackView stackViewWithViews:@[self.hostRow,self.portRow]];
    address.spacing = 16; address.alignment = NSLayoutAttributeBottom; address.distribution = NSStackViewDistributionFill;
    MirraAdd(self.form,address);
    self.account = [NSTextField textFieldWithString:@"mobile"]; MirraAdd(self.form, [self row:@"Account" control:self.account]);
    for (NSTextField *field in @[self.host,self.port,self.account]) { field.delegate = self; field.font = [NSFont systemFontOfSize:14]; }
    self.devices = [[NSPopUpButton alloc] init]; [self.devices addItemWithTitle:@"Select your jailbroken phone"];
    [self.devices.widthAnchor constraintGreaterThanOrEqualToConstant:140].active = YES;
    [self.devices setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    [self.devices setContentCompressionResistancePriority:250 forOrientation:NSLayoutConstraintOrientationHorizontal];
    self.devices.cell.lineBreakMode = NSLineBreakByTruncatingTail;
    self.scanButton = MirraAction(@"Scan", self, @selector(scanDevices:), NO);
    NSStackView *selector = [NSStackView stackViewWithViews:@[self.devices, self.scanButton]]; selector.spacing = 12;
    self.usbRow = [self row:@"Phone" control:selector]; MirraAdd(self.form, self.usbRow);
    self.formPanel = MirraCard(self.form,12); MirraAdd(stack,self.formPanel);
    self.fingerprint = MirraLabel(@"", 12, NSFontWeightRegular); self.fingerprint.font = [NSFont monospacedSystemFontOfSize:12 weight:NSFontWeightRegular]; MirraAdd(stack, self.fingerprint);
    self.fingerprint.lineBreakMode = NSLineBreakByCharWrapping; self.fingerprint.alignment = NSTextAlignmentCenter;
    self.passwordForm = MirraColumn(10); self.passwordForm.alignment = NSLayoutAttributeCenterX;
    self.passwordPrompt = MirraLabel(@"Phone SSH password", 13, NSFontWeightMedium); self.passwordPrompt.alignment = NSTextAlignmentCenter;
    MirraAdd(self.passwordForm, self.passwordPrompt);
    self.password = [[NSSecureTextField alloc] init]; self.password.placeholderString = @"SSH password";
    self.password.target = self; self.password.action = @selector(submitPassword:); [self.password.heightAnchor constraintEqualToConstant:32].active = YES; MirraAdd(self.passwordForm, self.password);
    self.password.accessibilityLabel = @"Phone SSH password";
    self.authorizeButton = MirraAction(@"Authorize", self, @selector(submitPassword:), YES); [self.authorizeButton.widthAnchor constraintEqualToConstant:240].active = YES;
    NSStackView *passwordButtons = [NSStackView stackViewWithViews:@[self.authorizeButton, MirraAction(@"Cancel", self, @selector(cancelPassword:), NO)]];
    passwordButtons.spacing = 12; [self.passwordForm addArrangedSubview:passwordButtons]; MirraAdd(stack, self.passwordForm); self.passwordForm.hidden = YES;
    self.progress = [[NSProgressIndicator alloc] init]; self.progress.style = NSProgressIndicatorStyleSpinning; self.progress.controlSize = NSControlSizeSmall; self.progress.displayedWhenStopped = NO;
    self.primary = MirraAction(@"Continue", self, @selector(advance:), YES); [self.primary.widthAnchor constraintEqualToConstant:240].active = YES;
    self.primary.keyEquivalent = @"\r";
    self.progress.translatesAutoresizingMaskIntoConstraints = NO; [self.primary addSubview:self.progress];
    [NSLayoutConstraint activateConstraints:@[[self.progress.leadingAnchor constraintEqualToAnchor:self.primary.leadingAnchor constant:16],
        [self.progress.centerYAnchor constraintEqualToAnchor:self.primary.centerYAnchor],
        [self.progress.widthAnchor constraintEqualToConstant:16], [self.progress.heightAnchor constraintEqualToConstant:16]]];
    NSStackView *buttons = [NSStackView stackViewWithViews:@[self.primary]]; self.actionRow = buttons; MirraAdd(stack,buttons);
    [self.primary.centerXAnchor constraintEqualToAnchor:buttons.centerXAnchor].active = YES;
    self.again = MirraAction(@"Change phone", self, @selector(startAgain:), NO); [stack addArrangedSubview:self.again];
    NSTextField *note = MirraLabel(@"The setup phone must be jailbroken, with SSH and ldid installed. The iPhone you mirror can stay unmodified. Mirra prepares the certificate for you.", 12, NSFontWeightRegular);
    note.textColor = NSColor.secondaryLabelColor; note.alignment = NSTextAlignmentCenter; MirraAdd(stack, note);
}
- (void)controlTextDidChange:(NSNotification *)notification { (void)notification; self.draftEdited = YES; }
- (void)requestPassword:(NSString *)prompt reply:(void (^)(NSString *))reply {
    self.passwordReply = reply; self.passwordForm.hidden = NO; self.actionRow.hidden = YES; self.again.hidden = YES;
    self.primary.keyEquivalent = @""; self.authorizeButton.keyEquivalent = @"\r";
    self.passwordPrompt.stringValue = prompt.length ? prompt : @"Phone SSH password";
    self.detail.stringValue = @"Enter the setup phone’s SSH password. It goes directly to SSH and is never saved.";
    [self.progress stopAnimation:nil]; [self.view.window makeFirstResponder:self.password];
}
- (void)submitPassword:(id)sender {
    (void)sender; if (!self.passwordReply) return;
    void (^reply)(NSString *) = self.passwordReply; self.passwordReply = nil;
    NSString *value = self.password.stringValue; self.password.stringValue = @"";
    self.passwordForm.hidden = YES; self.actionRow.hidden = NO; reply(value); [self render:self.state];
    self.primary.keyEquivalent = @"\r"; self.authorizeButton.keyEquivalent = @"";
}
- (void)cancelPassword:(id)sender {
    (void)sender; void (^reply)(NSString *) = self.passwordReply; self.passwordReply = nil;
    self.password.stringValue = @""; self.passwordForm.hidden = YES; self.actionRow.hidden = NO;
    self.primary.keyEquivalent = @"\r"; self.authorizeButton.keyEquivalent = @"";
    if (reply) reply(nil); [self render:self.state];
}
- (void)render:(NSDictionary *)state {
    BOOL newStep = ![self.state[@"phase"] isEqual:state[@"phase"]];
    self.state = state;
    if (self.logState) self.logState(state);
    NSString *phase = state[@"phase"] ?: @"new";
    NSDictionary *pages = @{
        @"loading": @[@"Checking saved setup", @"Looking for an existing setup so you can continue where you left off.", @"Check again"],
        @"new": @[@"Connect your setup phone", @"Use a separate jailbroken phone to enable Mirroring. Enter its SSH details below; no certificate file is needed.", @"Continue"],
        @"contact": @[@"Reach your setup phone", @"Keep your setup phone connected with SSH and its jailbreak running, then retry.", @"Retry connection"],
        @"trust": @[@"Verify this phone", @"Compare this host-key fingerprint with the one on your phone or in an already trusted SSH connection. Continue only if they match.", @"Trust this phone"],
        @"authorize": @[@"Allow secure access", @"Mirra will add its public key to your setup phone. Continue to authorize access; enter the SSH password here if asked.", @"Authorize SSH"],
        @"deploy": @[@"Prepare your connection", @"Mirra will install its helper and prepare secure access for this phone. It uses the phone’s certificate when available, or requests one automatically, then checks the connection.", @"Prepare connection"],
        @"ready": @[@"Finish setup", @"Your setup phone passed verification. Finish to use this connection and keep Mirra available when you sign in. Any current Mirroring session will close.", @"Finish setup"],
        @"installed": @[@"You’re ready", @"Keep your target iPhone locked and nearby. Connect to open Apple’s iPhone Mirroring and complete its authentication.", @"Connect"]
    };
    NSArray *page = pages[phase] ?: pages[@"new"];
    self.heading.stringValue = page[0]; self.detail.stringValue = page[1]; self.primary.title = page[2];
    if ([phase isEqual:@"installed"] && ![state[@"support_state"] isEqual:@"ready"]) {
        NSDictionary *report = [state[@"support_report"] isKindOfClass:NSDictionary.class] ? state[@"support_report"] : @{};
        self.heading.stringValue = [report[@"title"] isKindOfClass:NSString.class] ? report[@"title"] : @"Checking Mirroring support";
        self.detail.stringValue = [report[@"detail"] isKindOfClass:NSString.class] ? report[@"detail"] : @"Go back and check the home screen for the current connection and available actions.";
        self.primary.title = @"Check connection";
    }
    self.form.hidden = ![phase isEqual:@"new"];
    self.formPanel.hidden = self.form.hidden;
    self.fingerprint.hidden = ![phase isEqual:@"trust"];
    self.fingerprint.stringValue = [state[@"fingerprint"] isKindOfClass:NSString.class] ? state[@"fingerprint"] : @"";
    NSString *error = [state[@"error"] isKindOfClass:NSString.class] ? state[@"error"] : @"";
    if (!error.length) self.lastError = nil;
    else if (!self.busy && self.active && !self.previewing && self.failed && ![self.lastError isEqual:error]) {
        self.lastError = error; self.failed(error);
    }
    self.primary.enabled = !self.busy && !([phase isEqual:@"loading"] && self.checking); self.scanButton.enabled = !self.busy && !self.scanning; self.scanButton.title = self.scanning ? @"Scanning…" : @"Scan"; self.again.enabled = !self.busy;
    self.again.hidden = [phase isEqual:@"new"] || [phase isEqual:@"loading"];
    for (NSView *view in @[self.host, self.port, self.account, self.transport, self.devices]) [(NSControl *)view setEnabled:!self.busy];
    self.hostRow.hidden = self.transport.indexOfSelectedItem == 1;
    self.portRow.hidden = NO;
    self.usbRow.hidden = self.transport.indexOfSelectedItem != 1;
    if (self.busy && !self.passwordReply) [self.progress startAnimation:nil]; else [self.progress stopAnimation:nil];
    NSArray *phases = @[@"new", @"trust", @"authorize", @"deploy", @"ready"];
    NSUInteger step = [phases indexOfObject:phase]; if ([phase isEqual:@"contact"]) step = 0;
    self.steps.stringValue = step == NSNotFound ? ([phase isEqual:@"installed"] ? @"SETUP COMPLETE" : @"GETTING STARTED") : [NSString stringWithFormat:@"STEP %lu OF 5", (unsigned long)step + 1];
    if (self.busy && !self.passwordReply) { self.primary.title = @"Working…"; self.detail.stringValue = @"Keep the setup phone connected. This step may take a moment."; }
    if (self.changed) self.changed();
    if (newStep && self.active && !self.previewing && self.view.window) {
        [self.view.window.contentView layoutSubtreeIfNeeded];
        NSScrollView *scroll = self.view.enclosingScrollView; [scroll.contentView scrollToPoint:NSZeroPoint]; [scroll reflectScrolledClipView:scroll.contentView];
        if (!NSWorkspace.sharedWorkspace.accessibilityDisplayShouldReduceMotion) {
            self.view.alphaValue = 0; [NSAnimationContext runAnimationGroup:^(NSAnimationContext *context) { context.duration = .16; self.view.animator.alphaValue = 1; } completionHandler:nil];
        }
    }
}
- (void)defaults:(NSDictionary *)settings {
    if ([settings[@"host"] isKindOfClass:NSString.class]) self.host.stringValue = settings[@"host"];
    if ([settings[@"user"] isKindOfClass:NSString.class]) self.account.stringValue = settings[@"user"];
    if ([settings[@"port"] respondsToSelector:@selector(stringValue)]) self.port.stringValue = [settings[@"port"] stringValue];
    [self.transport selectItemAtIndex:[settings[@"transport"] isEqual:@"usb"] ? 1 : 0];
}
- (void)command:(NSString *)action payload:(NSDictionary *)payload {
    if (self.busy || self.previewing) return;
    if ([action isEqual:@"status"]) { if (self.checking) return; self.checking = YES; }
    else if ([action isEqual:@"devices"]) { if (self.scanning) return; self.scanning = YES; }
    else {
        self.busy = YES; self.revision++; if (self.logAction) self.logAction(action);
        NSMutableDictionary *state = [self.state mutableCopy];
        [state removeObjectForKey:@"error"]; [state removeObjectForKey:@"error_code"]; self.state = state;
    }
    NSUInteger revision = self.revision;
    [self render:self.state ?: @{@"phase": @"new"}];
    if ([action isEqual:@"connect"]) {
        __weak Wizard *weak = self;
        self.authorization = [[Authorization alloc] initWithPrompt:^(NSString *prompt, void (^reply)(NSString *)) { [weak requestPassword:prompt reply:reply]; }];
        if (!self.authorization) { self.busy = NO; NSMutableDictionary *failed = [self.state mutableCopy]; failed[@"error"] = @"Secure password entry could not start. Please try again."; [self render:failed]; return; }
    }
    NSDictionary *environment = self.authorization.environment;
    NSString *python = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:
        [NSBundle.mainBundle objectForInfoDictionaryKey:@"SupportPython"]].path;
    NSURL *script = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"Support/scripts/observe.py"];
    NSString *password = [[NSBundle.mainBundle.executableURL URLByDeletingLastPathComponent] URLByAppendingPathComponent:@"password"].path;
    NSData *input = [NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
    dispatch_async(self.queue, ^{
        NSTask *task = [[NSTask alloc] init]; task.executableURL = [NSURL fileURLWithPath:python];
        task.arguments = @[@"-E", @"-s", @"-B", script.path, @"setup", action, @"--askpass", password];
        if (environment) { NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy]; [env addEntriesFromDictionary:environment]; task.environment = env; }
        NSPipe *output = [NSPipe pipe]; NSPipe *stream = [NSPipe pipe]; task.standardOutput = output; task.standardInput = stream;
        task.standardError = NSFileHandle.fileHandleWithNullDevice;
        NSError *error = nil; BOOL launched = [task launchAndReturnError:&error];
        NSData *data = nil;
        if (launched) {
            @try { [stream.fileHandleForWriting writeData:input]; [stream.fileHandleForWriting closeFile]; } @catch (NSException *exception) { (void)exception; }
            data = [output.fileHandleForReading readDataToEndOfFile]; [task waitUntilExit];
        }
        NSDictionary *reply = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            [self finishReply:reply action:action revision:revision];
        });
    });
}
- (void)finishReply:(NSDictionary *)reply action:(NSString *)action revision:(NSUInteger)revision {
    BOOL observation = [action isEqual:@"status"] || [action isEqual:@"devices"];
            if ([action isEqual:@"status"]) self.checking = NO;
            if ([action isEqual:@"devices"]) self.scanning = NO;
            if (observation && (revision != self.revision || self.busy)) return;
            if (!observation) { [self.authorization invalidate]; self.authorization = nil; [self cancelPassword:nil]; self.busy = NO; }
            NSDictionary *value = [reply isKindOfClass:NSDictionary.class] ? reply[@"setup"] : nil;
            if (![value isKindOfClass:NSDictionary.class]) {
                NSMutableDictionary *failed = [self.state mutableCopy] ?: [NSMutableDictionary dictionaryWithObject:@"new" forKey:@"phase"];
                failed[@"error"] = @"The setup helper did not return a result. Keep the phone connected and retry this step."; [self render:failed]; return;
            }
            if ([action isEqual:@"devices"] && [value[@"devices"] isKindOfClass:NSArray.class]) {
                id previous = self.devices.selectedItem.representedObject;
                [self.devices removeAllItems];
                for (NSDictionary *device in value[@"devices"]) { [self.devices addItemWithTitle:device[@"title"]]; self.devices.lastItem.representedObject = device; }
                for (NSMenuItem *item in self.devices.itemArray) if ([item.representedObject isEqual:previous]) [self.devices selectItem:item];
                if (!self.devices.numberOfItems) [self.devices addItemWithTitle:@"No accessible USB phones found"];
                [self render:self.state]; return;
            }
            if (!self.loadedDefaults && !self.draftEdited && [value[@"phase"] isEqual:@"new"]) [self defaults:value[@"defaults"]];
            self.loadedDefaults = YES;
            [self render:value];
            if (self.active && [value[@"phase"] isEqual:@"installed"] && ![value[@"support_state"] isEqual:@"ready"]) {
                [self.timer invalidate]; self.timer = [NSTimer scheduledTimerWithTimeInterval:5 target:self selector:@selector(check:) userInfo:nil repeats:YES];
            } else { [self.timer invalidate]; self.timer = nil; }
}
- (void)advance:(id)sender {
    (void)sender; NSString *phase = self.state[@"phase"] ?: @"new";
    NSMutableDictionary *payload = [NSMutableDictionary dictionary]; if (self.state[@"id"]) payload[@"id"] = self.state[@"id"];
    NSString *action = @"status";
    if ([phase isEqual:@"new"]) {
        BOOL usb = self.transport.indexOfSelectedItem == 1;
        [payload addEntriesFromDictionary:@{@"transport": usb ? @"usb" : @"ssh", @"host": self.host.stringValue,
            @"port": self.port.stringValue, @"user": self.account.stringValue}];
        if (usb && self.devices.selectedItem.representedObject) [payload addEntriesFromDictionary:self.devices.selectedItem.representedObject];
        action = @"begin";
    } else if ([phase isEqual:@"contact"]) action = @"scan";
    else if ([phase isEqual:@"trust"]) { action = @"trust"; payload[@"fingerprint"] = self.state[@"fingerprint"]; }
    else if ([phase isEqual:@"authorize"]) action = @"connect";
    else if ([phase isEqual:@"deploy"]) action = @"provision";
    else if ([phase isEqual:@"ready"]) action = @"finish";
    else if ([phase isEqual:@"installed"] && [self.state[@"support_state"] isEqual:@"ready"]) {
        if (self.finished) self.finished(); return;
    }
    [self command:action payload:payload];
}
- (void)transportChanged:(id)sender { (void)sender; self.draftEdited = YES; [self render:self.state]; if (self.transport.indexOfSelectedItem == 1) [self scanDevices:nil]; }
- (void)scanDevices:(id)sender { (void)sender; [self command:@"devices" payload:@{}]; }
- (void)startAgain:(id)sender { (void)sender; if (self.busy) return; self.revision++; self.loadedDefaults = YES; [self.timer invalidate]; [self render:@{@"phase": @"new"}]; }
- (void)check:(id)sender { (void)sender; if (self.active) [self command:@"status" payload:@{}]; }
- (void)hide { self.active = NO; self.primary.keyEquivalent = @""; [self.timer invalidate]; self.timer = nil; }
- (void)show {
    [self build]; self.active = YES;
    self.primary.keyEquivalent = self.passwordReply ? @"" : @"\r";
    if (!self.state) { [self render:@{@"phase": @"loading"}]; [self command:@"status" payload:@{}]; }
    else if ([self.state[@"phase"] isEqual:@"installed"]) [self check:nil];
}
- (void)loadPreview:(NSDictionary *)state {
    self.previewing = YES; [self build]; self.busy = [state[@"busy"] boolValue];
    [self defaults:state[@"defaults"]]; [self render:state];
    if (state[@"password_prompt"]) { [self requestPassword:@"Phone SSH password" reply:^(NSString *password) { (void)password; }]; }
}
@end
