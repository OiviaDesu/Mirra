#import <AppKit/AppKit.h>

static NSColor *rgb(CGFloat r, CGFloat g, CGFloat b, CGFloat a) {
    return [NSColor colorWithSRGBRed:r green:g blue:b alpha:a];
}

static void drawIcon(void) {
    NSBezierPath *tile = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(52, 52, 920, 920) xRadius:208 yRadius:208];
    NSGradient *background = [[NSGradient alloc] initWithStartingColor:rgb(.10, .24, .31, 1) endingColor:rgb(.025, .065, .12, 1)];
    [background drawInBezierPath:tile angle:-65];
    [NSGraphicsContext saveGraphicsState]; [tile addClip];
    NSGradient *glow = [[NSGradient alloc] initWithStartingColor:rgb(.19, .77, .66, .22) endingColor:rgb(.19, .77, .66, 0)];
    [glow drawFromCenter:NSMakePoint(760, 820) radius:0 toCenter:NSMakePoint(600, 760) radius:670 options:0];
    [NSGraphicsContext restoreGraphicsState];

    NSBezierPath *back = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(215, 270, 280, 495) xRadius:66 yRadius:66];
    [rgb(.53, .75, .81, .13) setFill]; [back fill];
    [rgb(.61, .87, .87, .58) setStroke]; back.lineWidth = 24; [back stroke];

    NSBezierPath *front = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(495, 255, 306, 520) xRadius:74 yRadius:74];
    NSShadow *shadow = [[NSShadow alloc] init]; shadow.shadowColor = rgb(0, 0, 0, .24); shadow.shadowBlurRadius = 30; shadow.shadowOffset = NSMakeSize(0, -12);
    [NSGraphicsContext saveGraphicsState]; [shadow set];
    NSGradient *mint = [[NSGradient alloc] initWithStartingColor:rgb(.67, .98, .87, 1) endingColor:rgb(.20, .78, .68, 1)];
    [mint drawInBezierPath:front angle:-65]; [NSGraphicsContext restoreGraphicsState];
    NSBezierPath *screen = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(521, 286, 254, 454) xRadius:48 yRadius:48];
    [rgb(.035, .15, .19, 1) setFill]; [screen fill];

    // One continuous ribbon joins the two screens into Mirra's M.
    NSBezierPath *ribbon = [NSBezierPath bezierPath];
    [ribbon moveToPoint:NSMakePoint(318, 420)]; [ribbon lineToPoint:NSMakePoint(318, 619)];
    [ribbon curveToPoint:NSMakePoint(343, 632) controlPoint1:NSMakePoint(318, 640) controlPoint2:NSMakePoint(330, 643)];
    [ribbon lineToPoint:NSMakePoint(491, 508)]; [ribbon lineToPoint:NSMakePoint(647, 637)];
    [ribbon curveToPoint:NSMakePoint(674, 622) controlPoint1:NSMakePoint(660, 648) controlPoint2:NSMakePoint(674, 642)];
    [ribbon lineToPoint:NSMakePoint(674, 420)];
    ribbon.lineWidth = 44; ribbon.lineCapStyle = NSLineCapStyleRound; ribbon.lineJoinStyle = NSLineJoinStyleRound;
    [rgb(.92, 1, .98, 1) setStroke]; [ribbon stroke];
    [rgb(.57, .94, .82, 1) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(611, 321, 72, 9) xRadius:4.5 yRadius:4.5] fill];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) return 2;
        NSString *directory = [NSString stringWithUTF8String:argv[1]];
        if (![NSFileManager.defaultManager createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:NULL]) return 1;
        for (NSNumber *number in @[@16, @32, @128, @256, @512]) {
            NSInteger points = number.integerValue;
            for (NSInteger scale = 1; scale <= 2; scale++) {
                NSInteger size = points * scale;
                NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL pixelsWide:size pixelsHigh:size bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
                [NSGraphicsContext saveGraphicsState]; [NSGraphicsContext setCurrentContext:[NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap]];
                NSAffineTransform *transform = [NSAffineTransform transform]; [transform scaleBy:(CGFloat)size / 1024]; [transform concat];
                drawIcon(); [NSGraphicsContext restoreGraphicsState];
                NSString *name = [NSString stringWithFormat:@"icon_%ldx%ld%@.png", (long)points, (long)points, scale == 2 ? @"@2x" : @""];
                if (![[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:[directory stringByAppendingPathComponent:name] atomically:YES]) return 1;
            }
        }
    }
    return 0;
}
