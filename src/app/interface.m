#import "interface.h"
@implementation MirraSurface
- (void)drawRect:(NSRect)dirty { [NSColor.windowBackgroundColor setFill]; NSRectFill(dirty); }
- (void)viewDidChangeEffectiveAppearance { [super viewDidChangeEffectiveAppearance]; self.needsDisplay = YES; }
@end
@implementation MirraDocument
- (BOOL)isFlipped { return YES; }
@end
@interface MirraScrollView : NSScrollView
@property NSLayoutConstraint *documentWidth;
@property BOOL synchronizing;
@end
@implementation MirraScrollView
- (instancetype)initWithFrame:(NSRect)frame {
    if ((self = [super initWithFrame:frame])) {
        self.contentView.postsFrameChangedNotifications = YES;
        self.contentView.postsBoundsChangedNotifications = YES;
        for (NSString *name in @[NSViewFrameDidChangeNotification, NSViewBoundsDidChangeNotification])
            [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(clipChanged:) name:name object:self.contentView];
    }
    return self;
}
- (void)dealloc { [NSNotificationCenter.defaultCenter removeObserver:self]; }
- (void)clipChanged:(NSNotification *)notification { (void)notification; [self synchronizeDocumentWidth]; }
- (void)synchronizeDocumentWidth {
    if (self.synchronizing || !self.documentWidth || NSWidth(self.contentView.bounds) <= 0) return;
    self.synchronizing = YES;
    // Legacy scrollbars change the clip frame after the window's constraint
    // pass. Follow the actual viewport rather than its stale layout anchor.
    for (NSUInteger pass = 0; pass < 3; pass++) {
        CGFloat width = NSWidth(self.contentView.bounds);
        if (self.documentWidth.active && fabs(self.documentWidth.constant - width) < .1
            && fabs(NSWidth(((NSView *)self.documentView).frame) - width) < .1) break;
        self.documentWidth.constant = width; self.documentWidth.active = YES;
        NSView *document = self.documentView;
        [document setFrameSize:NSMakeSize(width, NSHeight(document.frame))];
        document.needsLayout = YES;
        [self.contentView layoutSubtreeIfNeeded];
    }
    self.synchronizing = NO;
}
- (void)tile { [super tile]; [self synchronizeDocumentWidth]; }
- (void)layout { [super layout]; [self synchronizeDocumentWidth]; }
@end
@implementation MirraPanel
- (void)drawRect:(NSRect)dirty {
    (void)dirty;
    NSBezierPath *shape = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, .5, .5) xRadius:12 yRadius:12];
    [[NSColor.labelColor colorWithAlphaComponent:.035] setFill]; [shape fill];
    [[NSColor.labelColor colorWithAlphaComponent:.07] setStroke]; shape.lineWidth = 1; [shape stroke];
}
- (void)viewDidChangeEffectiveAppearance { [super viewDidChangeEffectiveAppearance]; self.needsDisplay = YES; }
@end
@implementation MirraButton
- (BOOL)acceptsFirstResponder { return self.enabled; }
- (NSEdgeInsets)alignmentRectInsets {
    return self.image && self.imagePosition == NSImageOnly ? NSEdgeInsetsMake(0,0,0,0) : [super alignmentRectInsets];
}
- (NSFont *)titleFont { return [NSFont systemFontOfSize:self.subtle ? 11 : (self.prominent ? 16 : 13)
                                               weight:self.subtle ? NSFontWeightRegular : NSFontWeightSemibold]; }
- (NSSize)intrinsicContentSize {
    if (self.image && self.imagePosition == NSImageOnly) return NSMakeSize(34,34);
    CGFloat width = [self.title sizeWithAttributes:@{NSFontAttributeName:[self titleFont]}].width;
    return NSMakeSize(MAX(self.subtle ? 0 : 80, ceil(width) + (self.subtle ? 14 : 30)), self.subtle ? 22 : (self.prominent ? 48 : 34));
}
- (void)setSubtle:(BOOL)subtle { _subtle = subtle; [self invalidateIntrinsicContentSize]; self.needsDisplay = YES; }
- (void)setProminent:(BOOL)prominent { _prominent = prominent; [self invalidateIntrinsicContentSize]; self.needsDisplay = YES; }
- (void)setTitle:(NSString *)title { [super setTitle:title]; [self invalidateIntrinsicContentSize]; self.needsDisplay = YES; }
- (void)setImage:(NSImage *)image { [super setImage:image]; [self invalidateIntrinsicContentSize]; self.needsDisplay = YES; }
- (void)setEnabled:(BOOL)enabled { [super setEnabled:enabled]; self.needsDisplay = YES; }
- (void)viewDidChangeEffectiveAppearance { [super viewDidChangeEffectiveAppearance]; self.needsDisplay = YES; }
- (void)drawRect:(NSRect)dirty {
    (void)dirty;
    NSBezierPath *shape = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, 1, 1) xRadius:10 yRadius:10];
    NSColor *fill = self.prominent ? [NSColor colorWithSRGBRed:.44 green:.88 blue:.74 alpha:self.enabled ? 1 : .3] : NSColor.quaternaryLabelColor;
    if (self.highlighted && self.enabled) fill = [fill blendedColorWithFraction:.12 ofColor:NSColor.labelColor];
    if (!self.subtle || (self.highlighted && self.enabled)) { [fill setFill]; [shape fill]; }
    NSColor *ink = self.subtle ? NSColor.secondaryLabelColor : (self.prominent ? [NSColor colorWithSRGBRed:.025 green:.12 blue:.13 alpha:1] : NSColor.labelColor);
    NSDictionary *attrs = @{NSFontAttributeName: [self titleFont],
                            NSForegroundColorAttributeName: self.enabled ? ink : NSColor.tertiaryLabelColor};
    if (self.image && self.imagePosition == NSImageOnly) {
        NSColor *color = self.enabled ? ink : NSColor.tertiaryLabelColor;
        NSImage *image = [self.image imageWithSymbolConfiguration:[NSImageSymbolConfiguration configurationWithHierarchicalColor:color]];
        NSSize size = image.size;
        [image drawInRect:NSMakeRect((NSWidth(self.bounds)-size.width)/2, (NSHeight(self.bounds)-size.height)/2, size.width, size.height)];
    } else {
        NSSize size = [self.title sizeWithAttributes:attrs];
        [self.title drawAtPoint:NSMakePoint((self.bounds.size.width-size.width)/2, (self.bounds.size.height-size.height)/2) withAttributes:attrs];
    }
    if (self.window.firstResponder == self && self.window.keyWindow) {
        [NSGraphicsContext saveGraphicsState]; NSSetFocusRingStyle(NSFocusRingOnly); [shape fill]; [NSGraphicsContext restoreGraphicsState];
    }
}
@end
NSTextField *MirraLabel(NSString *value, CGFloat size, NSFontWeight weight) {
    NSTextField *view = [NSTextField wrappingLabelWithString:value]; view.font = [NSFont systemFontOfSize:size weight:weight];
    view.selectable = YES; return view;
}
MirraButton *MirraAction(NSString *title, id target, SEL action, BOOL prominent) {
    MirraButton *button = [[MirraButton alloc] init]; button.title = title; button.target = target; button.action = action;
    button.prominent = prominent; button.bordered = NO; button.focusRingType = NSFocusRingTypeExterior;
    return button;
}
MirraButton *MirraIconAction(NSString *symbol, NSString *label, id target, SEL action) {
    MirraButton *button = MirraAction(@"",target,action,NO);
    button.imagePosition = NSImageOnly;
    button.image = [[NSImage imageWithSystemSymbolName:symbol accessibilityDescription:label]
        imageWithSymbolConfiguration:[NSImageSymbolConfiguration configurationWithPointSize:17 weight:NSFontWeightMedium]];
    button.accessibilityLabel = label; button.toolTip = label;
    return button;
}
NSStackView *MirraColumn(CGFloat spacing) {
    NSStackView *stack = [[NSStackView alloc] init]; stack.orientation = NSUserInterfaceLayoutOrientationVertical;
    stack.alignment = NSLayoutAttributeLeading; stack.spacing = spacing; stack.translatesAutoresizingMaskIntoConstraints = NO;
    [stack setContentHuggingPriority:750 forOrientation:NSLayoutConstraintOrientationVertical];
    [stack setContentCompressionResistancePriority:750 forOrientation:NSLayoutConstraintOrientationVertical]; return stack;
}
NSView *MirraCard(NSStackView *stack, CGFloat inset) {
    NSView *panel = [[MirraPanel alloc] init]; MirraFill(stack, panel, inset); return panel;
}
NSView *MirraPage(NSStackView *stack, BOOL centered) {
    NSView *page = [[NSView alloc] init];
    MirraScrollView *scroll = [[MirraScrollView alloc] initWithFrame:NSZeroRect]; scroll.drawsBackground = NO;
    scroll.hasVerticalScroller = YES; scroll.autohidesScrollers = YES; scroll.horizontalScrollElasticity = NSScrollElasticityNone;
    MirraFill(scroll, page, 0);
    NSView *document = [[MirraDocument alloc] init]; document.translatesAutoresizingMaskIntoConstraints = NO;
    scroll.documentView = document; [document addSubview:stack];
    scroll.documentWidth = [document.widthAnchor constraintEqualToConstant:0];
    // The page is centered while it fits, and grows into a scrollable document when it doesn't.
    NSLayoutConstraint *viewportHeight = [document.heightAnchor constraintEqualToAnchor:scroll.contentView.heightAnchor]; viewportHeight.priority = 250;
    NSLayoutConstraint *position = centered ? [stack.centerYAnchor constraintEqualToAnchor:document.centerYAnchor]
                                          : [stack.topAnchor constraintEqualToAnchor:document.topAnchor constant:12];
    [NSLayoutConstraint activateConstraints:@[[document.heightAnchor constraintGreaterThanOrEqualToAnchor:scroll.contentView.heightAnchor], viewportHeight,
        [stack.leadingAnchor constraintEqualToAnchor:document.leadingAnchor constant:28],
        [stack.trailingAnchor constraintEqualToAnchor:document.trailingAnchor constant:-28], position,
        [stack.topAnchor constraintGreaterThanOrEqualToAnchor:document.topAnchor constant:12],
        [stack.bottomAnchor constraintLessThanOrEqualToAnchor:document.bottomAnchor constant:-12]]];
    return page;
}
void MirraFill(NSView *child, NSView *parent, CGFloat inset) {
    child.translatesAutoresizingMaskIntoConstraints = NO; [parent addSubview:child];
    [NSLayoutConstraint activateConstraints:@[[child.leadingAnchor constraintEqualToAnchor:parent.leadingAnchor constant:inset],
        [child.trailingAnchor constraintEqualToAnchor:parent.trailingAnchor constant:-inset],
        [child.topAnchor constraintEqualToAnchor:parent.topAnchor constant:inset], [child.bottomAnchor constraintEqualToAnchor:parent.bottomAnchor constant:-inset]]];
}
void MirraAdd(NSStackView *stack, NSView *view) {
    [stack addArrangedSubview:view]; [view.widthAnchor constraintEqualToAnchor:stack.widthAnchor].active = YES;
}
