#import <AppKit/AppKit.h>
@interface MirraSurface : NSView
@end
@interface MirraDocument : NSView
@end
@interface MirraPanel : NSView
@end
@interface MirraButton : NSButton
@property(nonatomic) BOOL prominent;
@property(nonatomic) BOOL subtle;
@end
NSTextField *MirraLabel(NSString *value, CGFloat size, NSFontWeight weight);
MirraButton *MirraAction(NSString *title, id target, SEL action, BOOL prominent);
MirraButton *MirraIconAction(NSString *symbol, NSString *label, id target, SEL action);
NSStackView *MirraColumn(CGFloat spacing);
NSView *MirraCard(NSStackView *stack, CGFloat inset);
NSView *MirraPage(NSStackView *stack, BOOL centered);
void MirraFill(NSView *child, NSView *parent, CGFloat inset);
void MirraAdd(NSStackView *stack, NSView *view);
