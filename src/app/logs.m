#import "logs.h"
#import "interface.h"

static const NSUInteger MirraLogEntryLimit = 400;
static const NSUInteger MirraLogCharacterLimit = 200000;
static const NSUInteger MirraNativeLogEntryLimit = 160;
static const NSUInteger MirraNativeLogCharacterLimit = 80000;
static const CGFloat MirraLogCollapsedHeight = 22;
static const CGFloat MirraLogExpandedHeight = 158;

@interface MirraLogs ()
@property(readwrite) NSStackView *view;
@property(readwrite) NSTextView *textView;
@property(readwrite) BOOL expanded;
@property(readwrite) BOOL paused;
@property(readwrite) BOOL verbose;
@property NSScrollView *scroll;
@property NSLayoutConstraint *height;
@property NSRect compactWindowFrame;
@property NSRect expandedWindowFrame;
@property NSTextField *status;
@property NSTextField *heading;
@property NSButton *expandButton;
@property NSButton *pauseButton;
@property NSButton *exportButton;
@property NSButton *verboseButton;
@property NSButton *directoryButton;
@property NSMutableArray<NSString *> *lines;
@property NSMutableArray<NSNumber *> *nativeLines;
@property NSUInteger characters;
@property NSUInteger nativeCount;
@property NSUInteger nativeCharacters;
@property NSDictionary *cursor;
@property NSString *lastReport;
@property NSString *lastSetup;
@property NSString *lastReaderError;
@property NSTimer *timer;
@property BOOL reading;
@property BOOL stopped;
@property NSUInteger generation;
@property NSDateFormatter *clock;
@property NSISO8601DateFormatter *timestamp;
@property NSTask *nativeTask;
@property NSPipe *nativeInput;
@property BOOL restartNative;
@property BOOL capturing;
@end

@implementation MirraLogs
- (NSButton *)smallButton:(NSString *)title action:(SEL)action {
    MirraButton *button = MirraAction(title,self,action,NO); button.subtle = YES; button.controlSize = NSControlSizeSmall;
    button.font = [NSFont systemFontOfSize:11]; return button;
}
- (instancetype)init {
    self = [super init]; if (!self) return nil;
    self.expanded = NO; self.lines = [NSMutableArray array]; self.nativeLines = [NSMutableArray array]; self.cursor = @{};
    self.clock = [[NSDateFormatter alloc] init]; self.clock.dateFormat = @"HH:mm:ss";
    self.timestamp = [[NSISO8601DateFormatter alloc] init];
    self.timestamp.formatOptions = NSISO8601DateFormatWithInternetDateTime | NSISO8601DateFormatWithFractionalSeconds;
    self.view = MirraColumn(6);
    self.heading = MirraLabel(@"Logs", 12, NSFontWeightMedium);
    self.status = MirraLabel(@"Live", 10, NSFontWeightRegular); self.status.textColor = NSColor.secondaryLabelColor;
    self.exportButton = [self smallButton:@"Copy" action:@selector(copyLogs:)]; self.exportButton.enabled = NO;
    self.pauseButton = [self smallButton:@"Pause" action:@selector(togglePaused:)];
    self.expandButton = [self smallButton:@"Show log" action:@selector(toggleExpanded:)];
    self.directoryButton = [self smallButton:@"Open log folder" action:@selector(openLogFolder:)];
    self.directoryButton.toolTip = @"Dated log files are kept for seven days, with bounded size backups.";
    self.verboseButton = [NSButton checkboxWithTitle:@"Verbose" target:self action:@selector(toggleVerbose:)];
    self.verboseButton.font = [NSFont systemFontOfSize:11];
    self.verboseButton.toolTip = @"Include routine macOS framework debug messages.";
    NSView *spacer = [[NSView alloc] init]; [spacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSStackView *header = [NSStackView stackViewWithViews:@[self.heading,self.status,spacer,self.verboseButton,self.exportButton,self.pauseButton,self.directoryButton,self.expandButton]];
    header.spacing = 6; header.alignment = NSLayoutAttributeCenterY; MirraAdd(self.view, header);
    [header.heightAnchor constraintEqualToConstant:MirraLogCollapsedHeight].active = YES;
    [self.expandButton.trailingAnchor constraintEqualToAnchor:header.trailingAnchor].active = YES;
    self.scroll = [[NSScrollView alloc] init]; self.scroll.hasVerticalScroller = YES;
    self.scroll.autohidesScrollers = YES; self.scroll.borderType = NSBezelBorder;
    self.scroll.drawsBackground = YES; self.scroll.backgroundColor = NSColor.textBackgroundColor;
    self.textView = [[NSTextView alloc] initWithFrame:NSMakeRect(0,0,500,130)];
    self.textView.editable = NO; self.textView.selectable = YES; self.textView.richText = NO;
    self.textView.font = [NSFont monospacedSystemFontOfSize:10.5 weight:NSFontWeightRegular];
    self.textView.textColor = NSColor.labelColor; self.textView.backgroundColor = NSColor.textBackgroundColor;
    self.textView.textContainerInset = NSMakeSize(8,8); self.textView.horizontallyResizable = NO;
    self.textView.verticallyResizable = YES; self.textView.autoresizingMask = NSViewWidthSizable;
    self.textView.minSize = NSMakeSize(0,130); self.textView.maxSize = NSMakeSize(CGFLOAT_MAX,CGFLOAT_MAX);
    self.textView.textContainer.widthTracksTextView = YES;
    self.textView.textContainer.containerSize = NSMakeSize(500,CGFLOAT_MAX);
    self.textView.accessibilityLabel = @"Mirra logs";
    self.scroll.documentView = self.textView; MirraAdd(self.view, self.scroll);
    [self.scroll.heightAnchor constraintEqualToConstant:130].active = YES;
    self.height = [self.view.heightAnchor constraintEqualToConstant:MirraLogCollapsedHeight]; self.height.active = YES;
    self.scroll.hidden = YES;
    for (NSView *item in @[self.heading,self.status,self.verboseButton,self.exportButton,self.pauseButton]) item.hidden = YES;
    return self;
}
- (void)render {
    if (self.paused || !self.expanded) return;
    NSRange selection = self.textView.selectedRange;
    BOOL atBottom = NSMaxY(self.scroll.contentView.bounds) >= self.textView.bounds.size.height - 24;
    BOOL follow = atBottom && selection.length == 0;
    NSPoint origin = self.scroll.contentView.bounds.origin;
    NSString *value = [self.lines componentsJoinedByString:@"\n"];
    self.textView.string = value;
    if (follow) [self.textView scrollRangeToVisible:NSMakeRange(value.length,0)];
    else {
        self.textView.selectedRange = NSMakeRange(MIN(selection.location,value.length), MIN(selection.length,value.length - MIN(selection.location,value.length)));
        [self.scroll.contentView scrollToPoint:origin]; [self.scroll reflectScrolledClipView:self.scroll.contentView];
    }
    self.exportButton.enabled = value.length > 0;
}
- (void)removeEntryAtIndex:(NSUInteger)index {
    NSUInteger length = self.lines[index].length;
    self.characters -= length;
    if (self.nativeLines[index].boolValue) { self.nativeCount--; self.nativeCharacters -= length; }
    [self.lines removeObjectAtIndex:index]; [self.nativeLines removeObjectAtIndex:index];
}
- (void)append:(NSString *)message source:(NSString *)source level:(NSString *)level time:(NSDate *)time historical:(BOOL)historical {
    if (![message isKindOfClass:NSString.class] || !message.length) return;
    if (message.length > 4000) message = [[message substringToIndex:4000] stringByAppendingString:@"…"];
    // A log entry stays one line; credentials/form values are never sent here.
    message = [[message componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet] componentsJoinedByString:@" "];
    NSDictionary *levels = @{@"debug":@"Debug",@"info":@"Info",@"warning":@"Warning",@"error":@"Error"};
    NSString *label = [level isKindOfClass:NSString.class] ? levels[level] : nil;
    if (![source isKindOfClass:NSString.class] || source.length > 24) source = @"Mirra";
    NSString *when = historical ? @"Earlier" : [self.clock stringFromDate:time ?: NSDate.date];
    NSString *line = [NSString stringWithFormat:@"%@ [%@] [%@] %@", when, label ?: @"Info", source, message];
    BOOL native = [source isEqual:@"Apple Mirroring"] || [source isEqual:@"AVConference"] || [source isEqual:@"VideoToolbox"] || [source isEqual:@"Decoder"];
    [self.lines addObject:line]; [self.nativeLines addObject:@(native)]; self.characters += line.length;
    if (native) { self.nativeCount++; self.nativeCharacters += line.length; }
    while (self.nativeCount > MirraNativeLogEntryLimit || self.nativeCharacters > MirraNativeLogCharacterLimit) {
        NSUInteger index = [self.nativeLines indexOfObject:@YES];
        if (index == NSNotFound) break; [self removeEntryAtIndex:index];
    }
    while (self.lines.count > MirraLogEntryLimit || self.characters > MirraLogCharacterLimit) {
        [self removeEntryAtIndex:0];
    }
}
- (void)recordMessage:(NSString *)message source:(NSString *)source level:(NSString *)level {
    [self append:message source:source level:level time:nil historical:NO]; [self render];
    if (self.capturing) [self persistEvent:@{@"message":message ?: @"",@"source":source ?: @"Mirra",@"level":level ?: @"info"}];
}
- (void)persistEvent:(NSDictionary *)event {
    NSString *pythonPath = [NSBundle.mainBundle objectForInfoDictionaryKey:@"SupportPython"];
    if (!pythonPath) return;
    NSURL *python = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:pythonPath];
    NSURL *script = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"Support/scripts/event.py"];
    NSData *data = [NSJSONSerialization dataWithJSONObject:event options:0 error:NULL];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0), ^{
        NSTask *task = [[NSTask alloc] init]; task.executableURL = python; task.arguments = @[@"-E",@"-s",@"-B",script.path];
        NSPipe *input = NSPipe.pipe; task.standardInput = input;
        task.standardOutput = NSFileHandle.fileHandleWithNullDevice; task.standardError = NSFileHandle.fileHandleWithNullDevice;
        if ([task launchAndReturnError:NULL]) {
            @try { [input.fileHandleForWriting writeData:data]; [input.fileHandleForWriting closeFile]; } @catch (NSException *error) { (void)error; }
            [task waitUntilExit];
        }
    });
}
- (void)recordReport:(NSDictionary *)report {
    NSString *state = [report[@"state"] isKindOfClass:NSString.class] ? report[@"state"] : nil;
    if (!state) return;
    NSString *detail = [report[@"detail"] isKindOfClass:NSString.class] ? report[@"detail"] : @"";
    NSString *signature = [NSString stringWithFormat:@"%@|%@", state, detail];
    if ([signature isEqual:self.lastReport]) return; self.lastReport = signature;
    NSDictionary *selected = @{@"state":state,@"detail":detail};
    NSString *value = [[NSString alloc] initWithData:[NSJSONSerialization dataWithJSONObject:selected options:0 error:NULL] encoding:NSUTF8StringEncoding];
    [self recordMessage:value source:@"Connection" level:@"debug"];
}
- (void)recordSetup:(NSDictionary *)state {
    NSString *phase = [state[@"phase"] isKindOfClass:NSString.class] ? state[@"phase"] : @"loading";
    NSString *error = [state[@"error"] isKindOfClass:NSString.class] ? state[@"error"] : @"";
    NSString *code = [state[@"error_code"] isKindOfClass:NSString.class] ? state[@"error_code"] : @"";
    NSString *signature = [NSString stringWithFormat:@"%@|%@|%@", phase,error,code];
    if ([signature isEqual:self.lastSetup]) return; self.lastSetup = signature;
    NSDictionary *selected = @{@"phase":phase,@"error":error,@"error_code":code};
    NSString *value = [[NSString alloc] initWithData:[NSJSONSerialization dataWithJSONObject:selected options:0 error:NULL] encoding:NSUTF8StringEncoding];
    [self recordMessage:value source:@"Setup state" level:error.length ? @"error" : @"debug"];
}
- (void)recordAction:(NSString *)action setup:(BOOL)setup {
    if ([action isEqual:@"status"] || [action isEqual:@"devices"]) return;
    [self recordMessage:[@"action=" stringByAppendingString:action] source:setup ? @"Setup" : @"Service" level:@"debug"];
}
- (void)applySnapshot:(NSDictionary *)snapshot {
    if (![snapshot isKindOfClass:NSDictionary.class] || ![snapshot[@"entries"] isKindOfClass:NSArray.class]) {
        NSString *message = @"The log helper did not return a snapshot. It will retry automatically.";
        if (![self.lastReaderError isEqual:message]) [self recordMessage:message source:@"Logs" level:@"warning"];
        self.lastReaderError = message; return;
    }
    if ([snapshot[@"cursor"] isKindOfClass:NSDictionary.class]) self.cursor = snapshot[@"cursor"];
    if ([snapshot[@"ok"] boolValue]) self.lastReaderError = nil;
    for (id item in [snapshot[@"entries"] subarrayWithRange:NSMakeRange(0,MIN(160,[snapshot[@"entries"] count]))]) {
        if (![item isKindOfClass:NSDictionary.class]) continue;
        NSString *message = item[@"message"];
        if (![message isKindOfClass:NSString.class]) continue;
        if (![snapshot[@"ok"] boolValue] && [message isEqual:self.lastReaderError]) continue;
        id stamp = item[@"observed_at"];
        NSDate *date = [stamp isKindOfClass:NSString.class] ? [self.timestamp dateFromString:stamp] : nil;
        [self append:message source:item[@"source"] level:item[@"level"] time:date historical:stamp == NSNull.null || !stamp];
        if (![snapshot[@"ok"] boolValue]) self.lastReaderError = message;
    }
    [self render];
}
- (void)refresh {
    if (self.stopped || self.reading) return;
    self.reading = YES; NSUInteger generation = self.generation;
    NSDictionary *cursor = self.cursor;
    NSString *pythonPath = [NSBundle.mainBundle objectForInfoDictionaryKey:@"SupportPython"];
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    if (!pythonPath || !resources) { self.reading = NO; [self applySnapshot:nil]; return; }
    NSURL *python = [resources URLByAppendingPathComponent:pythonPath];
    NSURL *script = [resources URLByAppendingPathComponent:@"Support/scripts/diagnostics.py"];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0), ^{
        NSTask *task = [[NSTask alloc] init]; task.executableURL = python;
        task.arguments = @[@"-E",@"-s",@"-B",script.path];
        NSPipe *output = NSPipe.pipe; NSPipe *input = NSPipe.pipe;
        task.standardOutput = output; task.standardInput = input; task.standardError = NSFileHandle.fileHandleWithNullDevice;
        NSData *data = nil;
        if ([task launchAndReturnError:NULL]) {
            @try {
                [input.fileHandleForWriting writeData:[NSJSONSerialization dataWithJSONObject:cursor options:0 error:NULL]];
                [input.fileHandleForWriting closeFile];
                data = [output.fileHandleForReading readDataToEndOfFile]; [task waitUntilExit];
            } @catch (NSException *error) { (void)error; }
        }
        NSDictionary *snapshot = data.length <= 1048576 ? [NSJSONSerialization JSONObjectWithData:data ?: NSData.data options:0 error:NULL] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            self.reading = NO; if (self.stopped || self.generation != generation) return;
            [self applySnapshot:snapshot];
        });
    });
}
- (void)tick:(NSTimer *)timer { (void)timer; if (self.view.window.visible) [self refresh]; }
- (void)startNativeStream {
    if (self.stopped || self.nativeTask) return;
    NSString *pythonPath = [NSBundle.mainBundle objectForInfoDictionaryKey:@"SupportPython"];
    if (!pythonPath) return;
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:pythonPath];
    NSMutableArray *arguments = [NSMutableArray arrayWithObjects:@"-E",@"-s",@"-B",[NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"Support/scripts/logstream.py"].path,nil];
    if (self.verbose) [arguments addObject:@"--verbose"];
    task.arguments = arguments;
    NSPipe *output = NSPipe.pipe; self.nativeInput = NSPipe.pipe;
    task.standardOutput = output; task.standardInput = self.nativeInput; task.standardError = NSFileHandle.fileHandleWithNullDevice;
    NSError *launchError = nil;
    if (![task launchAndReturnError:&launchError]) {
        [self recordMessage:[NSString stringWithFormat:@"System log reader launch failed: %@ code=%ld",launchError.domain,(long)launchError.code] source:@"Logs" level:@"error"]; return;
    }
    self.nativeTask = task; NSUInteger generation = self.generation;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0), ^{
        NSMutableData *buffer = [NSMutableData data];
        @try {
            while (YES) {
                NSData *chunk = output.fileHandleForReading.availableData; if (!chunk.length) break;
                [buffer appendData:chunk];
                const uint8_t newline = '\n'; NSData *delimiter = [NSData dataWithBytes:&newline length:1];
                NSRange range;
                while ((range = [buffer rangeOfData:delimiter options:0 range:NSMakeRange(0,buffer.length)]).location != NSNotFound) {
                    NSData *line = [buffer subdataWithRange:NSMakeRange(0,range.location)];
                    [buffer replaceBytesInRange:NSMakeRange(0,range.location+1) withBytes:NULL length:0];
                    NSDictionary *snapshot = [NSJSONSerialization JSONObjectWithData:line options:0 error:NULL];
                    dispatch_async(dispatch_get_main_queue(), ^{
                        if (!self.stopped && self.generation == generation) [self applySnapshot:snapshot];
                    });
                }
                if (buffer.length > 1048576) [buffer setLength:0];
            }
        } @catch (NSException *error) { (void)error; }
        [task waitUntilExit];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.nativeTask == task) {
                self.nativeTask = nil;
                if (self.restartNative && !self.stopped) { self.restartNative = NO; [self startNativeStream]; }
            }
        });
    });
}
- (void)start {
    if (self.timer) return; self.stopped = NO; self.generation++;
    self.capturing = YES;
    [self persistEvent:@{@"message":@"Mirra log capture started",@"source":@"Mirra",@"level":@"info"}];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:2 target:self selector:@selector(tick:) userInfo:nil repeats:YES];
    self.timer.tolerance = .4; [self refresh];
    [self startNativeStream];
}
- (void)stop {
    self.stopped = YES; self.capturing = NO; self.restartNative = NO; self.generation++; [self.timer invalidate]; self.timer = nil;
    @try { [self.nativeInput.fileHandleForWriting closeFile]; } @catch (NSException *error) { (void)error; }
    self.nativeInput = nil;
}
- (void)toggleExpanded:(id)sender {
    (void)sender; NSWindow *window = self.view.window; CGFloat previous = self.height.constant;
    self.expanded = !self.expanded; self.scroll.hidden = !self.expanded;
    for (NSView *item in @[self.heading,self.status,self.verboseButton,self.exportButton,self.pauseButton]) item.hidden = !self.expanded;
    self.height.constant = self.expanded ? MirraLogExpandedHeight : MirraLogCollapsedHeight; self.expandButton.title = self.expanded ? @"Hide log" : @"Show log";
    if (window) {
        NSRect frame = window.frame;
        if (self.expanded) {
            self.compactWindowFrame = frame;
            CGFloat delta = self.height.constant - previous; frame.origin.y -= delta; frame.size.height += delta;
        } else {
            NSPoint movement = NSMakePoint(frame.origin.x - self.expandedWindowFrame.origin.x,
                                            NSMaxY(frame) - NSMaxY(self.expandedWindowFrame));
            frame = self.compactWindowFrame; frame.origin.x += movement.x; frame.origin.y += movement.y;
        }
        NSRect visible = window.screen.visibleFrame;
        if (visible.size.height > 0) {
            frame.size.height = MIN(frame.size.height,visible.size.height);
            frame.origin.y = MAX(visible.origin.y,MIN(frame.origin.y,NSMaxY(visible)-frame.size.height));
        }
        [window setFrame:frame display:YES animate:NO];
        if (self.expanded) self.expandedWindowFrame = window.frame;
        [window.contentView layoutSubtreeIfNeeded];
    }
    if (self.expanded) [self render];
}
- (void)toggleVerbose:(id)sender {
    (void)sender; self.verbose = !self.verbose; self.verboseButton.state = self.verbose ? NSControlStateValueOn : NSControlStateValueOff;
    if (!self.timer || self.stopped) return;
    if (self.nativeTask) {
        self.restartNative = YES;
        @try { [self.nativeInput.fileHandleForWriting closeFile]; } @catch (NSException *error) { (void)error; }
        self.nativeInput = nil;
    } else [self startNativeStream];
}
- (void)togglePaused:(id)sender {
    (void)sender; self.paused = !self.paused; self.pauseButton.title = self.paused ? @"Resume" : @"Pause";
    self.status.stringValue = self.paused ? @"Paused" : @"Live";
    if (!self.paused) { [self render]; [self.textView scrollRangeToVisible:NSMakeRange(self.textView.string.length,0)]; }
}
- (void)copyLogs:(id)sender {
    (void)sender; [self copyToPasteboard:NSPasteboard.generalPasteboard];
}
- (void)copyToPasteboard:(NSPasteboard *)pasteboard {
    if (!self.textView.string.length) return;
    [pasteboard clearContents];
    [pasteboard setString:self.textView.string forType:NSPasteboardTypeString];
}
- (NSURL *)logDirectoryURL { return [NSURL fileURLWithPath:[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Mirra/logs"] isDirectory:YES]; }
- (BOOL)revealDirectory:(NSURL *)directory { return [NSWorkspace.sharedWorkspace openURL:directory]; }
- (void)openLogFolder:(id)sender {
    (void)sender; NSURL *directory = [self logDirectoryURL]; NSError *error = nil;
    for (NSURL *item in @[directory.URLByDeletingLastPathComponent,directory]) {
        BOOL isDirectory = NO;
        if (![NSFileManager.defaultManager fileExistsAtPath:item.path isDirectory:&isDirectory]) {
            if (![NSFileManager.defaultManager createDirectoryAtURL:item withIntermediateDirectories:NO attributes:@{NSFilePosixPermissions:@0700} error:&error]) break;
        } else if (!isDirectory) { error = [NSError errorWithDomain:NSCocoaErrorDomain code:NSFileWriteFileExistsError userInfo:nil]; break; }
    }
    if (error || ![self revealDirectory:directory]) {
        [self recordMessage:[NSString stringWithFormat:@"Open log folder failed: %@ code=%ld",error.domain ?: @"NSWorkspace",(long)error.code] source:@"Logs" level:@"error"];
    }
}
@end
