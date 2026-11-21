#import "jard_annotation_ink.h"
#import <AVFoundation/AVFoundation.h>
#import <CoreImage/CoreImage.h>
#import <CoreText/CoreText.h>
#import <ImageIO/ImageIO.h>

NSRect JardAnnotationAppKitRect(CGRect quartz, CGFloat primaryHeight) {
    return NSMakeRect(CGRectGetMinX(quartz), primaryHeight - CGRectGetMaxY(quartz),
                      CGRectGetWidth(quartz), CGRectGetHeight(quartz));
}


NSDictionary *JardAnnotationRoutingOrigin(NSDictionary *snapshot, NSDictionary *selectedApp, pid_t selectedPID,
    NSString *selectedTitle, CGRect captured) {
    NSValue *bounds = snapshot[@"bounds"];
    CGRect focused = bounds ? bounds.rectValue : CGRectZero;
    NSDictionary *previousApp = snapshot[@"app"];
    if (!selectedApp) {
        NSPoint center = NSMakePoint(CGRectGetMidX(focused), CGRectGetMidY(focused));
        return previousApp && !CGRectIsEmpty(focused) && CGRectContainsPoint(captured, center) ? previousApp : nil;
    }
    BOOL sameWindow = [snapshot[@"pid"] intValue] == selectedPID && selectedTitle.length &&
        [snapshot[@"title"] isEqual:selectedTitle] && !CGRectIsEmpty(focused) &&
        CGRectContainsRect(CGRectInset(captured, -4, -4), focused) &&
        CGRectContainsRect(CGRectInset(focused, -4, -4), captured);
    if (sameWindow && (previousApp[@"site"] || previousApp[@"terminal"])) {
        NSMutableDictionary *origin = selectedApp.mutableCopy;
        if (previousApp[@"site"]) origin[@"site"] = previousApp[@"site"];
        if (previousApp[@"terminal"]) origin[@"terminal"] = previousApp[@"terminal"];
        return origin;
    }
    return selectedApp;
}

NSPoint JardAnnotationUnitPoint(NSPoint point, NSSize bounds) {
    return NSMakePoint(fmin(1, fmax(0, point.x / fmax(1, bounds.width))),
                       fmin(1, fmax(0, point.y / fmax(1, bounds.height))));
}

NSDictionary *JardAnnotationEdge(NSPoint from, NSPoint to, NSTimeInterval time) {
    return @{@"from": [NSValue valueWithPoint:from], @"to": [NSValue valueWithPoint:to], @"time": @(time)};
}

NSArray<NSNumber *> *JardAnnotationFrameTimes(NSArray<NSDictionary *> *events, NSTimeInterval duration) {
    if (!isfinite(duration) || duration <= 0) return @[];
    double inset = MIN(.12, duration / 8), end = duration - inset;
    if (duration < .4) return @[@(duration / 2)];
    NSMutableArray<NSNumber *> *marks = NSMutableArray.array;
    double last = -1;
    for (NSDictionary *event in events) {
        if ([event[@"clear"] boolValue]) {
            if (last >= 0) [marks addObject:@(last)];
            last = -1;
            continue;
        }
        double time = MIN(end, [event[@"time"] doubleValue]);
        if (last >= 0 && time - last > .65) [marks addObject:@(last)];
        last = time;
    }
    if (last >= 0) [marks addObject:@(last)];
    NSMutableArray<NSNumber *> *times = [@[@(inset), @(end)] mutableCopy];
    for (NSUInteger i = 0, picked = MIN(4, marks.count); i < picked; i++) {
        NSUInteger row = (NSUInteger)floor((i + .5) * marks.count / picked);
        double time = MIN(end, marks[row].doubleValue + .08);
        if (time > inset + .15 && time < end - .15) [times addObject:@(time)];
    }
    NSUInteger count = MIN(6, MAX(2, (NSInteger)ceil(duration / 2) + 1));
    for (NSUInteger i = 1; i + 1 < count && times.count < count; i++) {
        double time = inset + (end - inset) * i / (count - 1);
        BOOL nearby = NO;
        for (NSNumber *selected in times) if (fabs(selected.doubleValue - time) < .18) { nearby = YES; break; }
        if (!nearby) [times addObject:@(time)];
    }
    [times sortUsingSelector:@selector(compare:)];
    NSMutableArray<NSNumber *> *distinct = NSMutableArray.array;
    for (NSNumber *time in times) {
        if (!distinct.count || time.doubleValue - distinct.lastObject.doubleValue > .12) [distinct addObject:time];
    }
    return distinct;
}

static NSInteger frameTenths(NSTimeInterval time) {
    return (NSInteger)floor(time * 10 + .0001);
}

static void drawFrameTime(CGContextRef context, NSTimeInterval time, CGPoint location) {
    long tenths = frameTenths(time), seconds = tenths / 10, minutes = seconds / 60;
    NSString *label = minutes < 60
        ? [NSString stringWithFormat:@"%ld:%02ld.%ld", minutes, seconds % 60, tenths % 10]
        : [NSString stringWithFormat:@"%ld:%02ld:%02ld.%ld", minutes / 60, minutes % 60, seconds % 60, tenths % 10];
    CTFontRef font = CTFontCreateWithName(CFSTR("Menlo"), 14, NULL);
    NSAttributedString *value = [[NSAttributedString alloc] initWithString:label attributes:@{
        (__bridge NSString *)kCTFontAttributeName: (__bridge id)font,
        (__bridge NSString *)kCTForegroundColorFromContextAttributeName: @YES}];
    CTLineRef line = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)value);
    CGContextSetRGBFillColor(context, .84, .87, .91, 1);
    CGContextSetTextPosition(context, location.x, location.y);
    CTLineDraw(line, context);
    CFRelease(line);
    CFRelease(font);
}

static NSArray<NSNumber *> *writeContactSheet(AVURLAsset *asset, NSURL *path, NSArray<NSNumber *> *times) {
    if (!times.count) return @[];
    AVAssetImageGenerator *generator = [AVAssetImageGenerator assetImageGeneratorWithAsset:asset];
    generator.appliesPreferredTrackTransform = YES;
    generator.maximumSize = CGSizeMake(420, 248);
    generator.requestedTimeToleranceBefore = generator.requestedTimeToleranceAfter = CMTimeMake(1, 5);
    NSMutableArray *images = NSMutableArray.array;
    NSMutableArray<NSNumber *> *sampled = NSMutableArray.array;
    for (NSNumber *seconds in times) {
        CMTime actual = kCMTimeInvalid;
        CGImageRef frame = [generator copyCGImageAtTime:CMTimeMakeWithSeconds(seconds.doubleValue, 600) actualTime:&actual error:nil];
        if (!frame) continue;
        double time = CMTimeGetSeconds(actual);
        if (!isfinite(time) || time < 0 || (sampled.count && frameTenths(time) <= frameTenths(sampled.lastObject.doubleValue))) {
            CGImageRelease(frame);
            continue;
        }
        [images addObject:CFBridgingRelease(frame)];
        [sampled addObject:@(time)];
    }
    if (!images.count) return @[];
    const size_t cellWidth = 436, cellHeight = 282;
    size_t columns = MIN(2, images.count), rows = (images.count + columns - 1) / columns;
    size_t width = columns * cellWidth, height = rows * cellHeight;
    CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef context = CGBitmapContextCreate(NULL, width, height, 8, 0, space, (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space);
    if (!context) return @[];
    CGContextSetRGBFillColor(context, .11, .13, .16, 1);
    CGContextFillRect(context, CGRectMake(0, 0, width, height));
    CGContextSetInterpolationQuality(context, kCGInterpolationHigh);
    for (NSUInteger i = 0; i < images.count; i++) {
        CGFloat x = (i % columns) * cellWidth, y = height - (1 + i / columns) * cellHeight;
        CGContextSetRGBFillColor(context, .05, .07, .09, 1);
        CGContextFillRect(context, CGRectMake(x + 8, y + 26, 420, 248));
        CGImageRef frame = (__bridge CGImageRef)images[i];
        CGFloat w = CGImageGetWidth(frame), h = CGImageGetHeight(frame);
        CGFloat scale = MIN(420.0 / w, 248.0 / h);
        w *= scale;
        h *= scale;
        CGContextDrawImage(context, CGRectMake(x + (cellWidth - w) / 2, y + 26 + (248 - h) / 2, w, h), frame);
        drawFrameTime(context, sampled[i].doubleValue, CGPointMake(x + 8, y + 8));
    }
    CGImageRef sheet = CGBitmapContextCreateImage(context);
    CGContextRelease(context);
    NSMutableData *png = NSMutableData.data;
    CGImageDestinationRef output = CGImageDestinationCreateWithData((__bridge CFMutableDataRef)png, CFSTR("public.png"), 1, NULL);
    BOOL saved = NO;
    if (sheet && output) {
        CGImageDestinationAddImage(output, sheet, NULL);
        if (CGImageDestinationFinalize(output)) saved = [png writeToURL:path options:NSDataWritingAtomic error:nil];
    }
    if (output) CFRelease(output);
    if (sheet) CGImageRelease(sheet);
    return saved ? sampled : @[];
}

void JardWriteAnnotationAssets(NSURL *movie, NSString *source, NSDictionary *origin, NSArray<NSDictionary *> *events, void (^completion)(NSError *)) {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @autoreleasepool {
            NSURL *folder = movie.URLByDeletingLastPathComponent;
            AVURLAsset *asset = [AVURLAsset URLAssetWithURL:movie options:nil];
            NSArray *times = JardAnnotationFrameTimes(events, CMTimeGetSeconds(asset.duration));
            NSArray *frames = writeContactSheet(asset, [folder URLByAppendingPathComponent:@"contact-sheet.png"], times);
            NSMutableArray *serialized = [NSMutableArray arrayWithCapacity:events.count];
            for (NSDictionary *event in events) {
                if ([event[@"clear"] boolValue]) { [serialized addObject:event]; continue; }
                NSPoint from = [event[@"from"] pointValue], to = [event[@"to"] pointValue];
                [serialized addObject:@{@"time": event[@"time"], @"stroke": event[@"stroke"] ?: @0,
                    @"from": @[@(from.x), @(from.y)], @"to": @[@(to.x), @(to.y)]}];
            }
            NSMutableDictionary *manifest = [@{@"version": @1, @"source": source, @"coordinateSpace": @"normalized-bottom-left",
                                               @"events": serialized, @"frames": frames} mutableCopy];
            if (origin.count) manifest[@"origin"] = origin;
            NSError *error = nil;
            NSData *json = [NSJSONSerialization dataWithJSONObject:manifest options:0 error:&error];
            if (json) [json writeToURL:[folder URLByAppendingPathComponent:@"annotation.json"] options:NSDataWritingAtomic error:&error];
            dispatch_async(dispatch_get_main_queue(), ^{ completion(error); });
        }
    });
}

static NSUInteger eventCount(NSArray<NSDictionary *> *events, NSTimeInterval time) {
    NSUInteger first = 0, end = events.count;
    while (first < end) {
        NSUInteger middle = first + (end - first) / 2;
        if ([events[middle][@"time"] doubleValue] <= time) first = middle + 1;
        else end = middle;
    }
    return first;
}

static CIImage *inkImage(NSArray<NSDictionary *> *events, NSUInteger count, CGRect frame) {
    NSUInteger start = count;
    while (start > 0 && !events[start - 1][@"clear"]) start--;
    if (start == count) return nil;
    size_t width = CGRectGetWidth(frame), height = CGRectGetHeight(frame);
    if (!width || !height) return nil;
    CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef context = CGBitmapContextCreate(NULL, width, height, 8, 0, space, (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space);
    if (!context) return nil;
    CGContextSetRGBStrokeColor(context, 1, .4, .22, 1);
    CGContextSetLineCap(context, kCGLineCapRound);
    CGContextSetLineWidth(context, MAX(3, MIN(width, height) * .007));
    for (NSUInteger i = start; i < count; i++) {
        NSPoint from = [events[i][@"from"] pointValue], to = [events[i][@"to"] pointValue];
        CGContextMoveToPoint(context, from.x * width, from.y * height);
        CGContextAddLineToPoint(context, to.x * width, to.y * height);
    }
    CGContextStrokePath(context);
    CGImageRef bitmap = CGBitmapContextCreateImage(context);
    CGContextRelease(context);
    if (!bitmap) return nil;
    CIImage *image = [[CIImage alloc] initWithCGImage:bitmap];
    CGImageRelease(bitmap);
    return [image imageByApplyingTransform:CGAffineTransformMakeTranslation(frame.origin.x, frame.origin.y)];
}

static NSError *renderError(void) {
    return [NSError errorWithDomain:@"JardAnnotation" code:1 userInfo:
        @{NSLocalizedDescriptionKey: @"Could not render the annotation’s drawing layer."}];
}

void JardRenderAnnotation(NSURL *input, NSURL *output, NSArray<NSDictionary *> *events, void (^completion)(NSError *)) {
    AVURLAsset *asset = [AVURLAsset URLAssetWithURL:input options:nil];
    NSLock *cacheLock = NSLock.new;
    __block NSUInteger cachedCount = NSUIntegerMax;
    __block CGRect cachedSize = CGRectZero;
    __block CIImage *cachedInk;
    [AVMutableVideoComposition videoCompositionWithAsset:asset applyingCIFiltersWithHandler:^(AVAsynchronousCIImageFilteringRequest *request) {
        @autoreleasepool {
            CIImage *video = request.sourceImage;
            NSUInteger count = eventCount(events, CMTimeGetSeconds(request.compositionTime));
            [cacheLock lock];
            if (count != cachedCount || !CGRectEqualToRect(cachedSize, video.extent)) {
                cachedCount = count;
                cachedSize = video.extent;
                cachedInk = inkImage(events, count, video.extent);
            }
            CIImage *ink = cachedInk;
            [cacheLock unlock];
            if (count && !events[count - 1][@"clear"] && !ink) {
                [request finishWithError:renderError()];
                return;
            }
            [request finishWithImage:ink ? [ink imageByCompositingOverImage:video] : video context:nil];
        }
    } completionHandler:^(AVMutableVideoComposition *composition, NSError *error) {
        if (!composition) { completion(error ?: renderError()); return; }
        AVAssetExportSession *exporter = [[AVAssetExportSession alloc] initWithAsset:asset presetName:AVAssetExportPresetHighestQuality];
        if (!exporter) { completion(renderError()); return; }
        exporter.videoComposition = composition;
        exporter.outputURL = output;
        exporter.outputFileType = AVFileTypeMPEG4;
        [exporter exportAsynchronouslyWithCompletionHandler:^{
            completion(exporter.status == AVAssetExportSessionStatusCompleted ? nil : exporter.error ?: renderError());
        }];
    }];
}
