#import "jard_recording_environment.h"
#import "jard_dictation_media.h"
#import <CoreAudio/CoreAudio.h>
#import <IOKit/pwr_mgt/IOPMLib.h>
#import <math.h>
#import <stddef.h>

static const AudioObjectPropertySelector virtualMainVolume = 'vmvc';

static AudioObjectPropertyAddress volumeAddress(AudioObjectPropertySelector selector, AudioObjectPropertyElement element) {
    return (AudioObjectPropertyAddress){selector, kAudioObjectPropertyScopeOutput, element};
}

static BOOL readProperty(AudioObjectID object, AudioObjectPropertyAddress address, UInt32 size, void *value) {
    UInt32 bytes = size;
    return AudioObjectGetPropertyData(object, &address, 0, NULL, &bytes, value) == noErr && bytes == size;
}

static NSNumber *readVolume(AudioObjectID device, AudioObjectPropertyAddress address) {
    Float32 value = 0;
    if (!readProperty(device, address, sizeof(value), &value) || !isfinite(value) || value < 0 || value > 1) return nil;
    return @(value);
}

static BOOL settableVolume(AudioObjectID device, AudioObjectPropertyAddress address) {
    Boolean writable = false;
    return AudioObjectIsPropertySettable(device, &address, &writable) == noErr && writable && readVolume(device, address);
}

static BOOL writeVolume(AudioObjectID device, AudioObjectPropertyAddress address, Float32 volume) {
    return AudioObjectSetPropertyData(device, &address, 0, NULL, sizeof(volume), &volume) == noErr;
}

static NSUInteger outputChannels(AudioObjectID device) {
    AudioObjectPropertyAddress address = volumeAddress(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyElementMain);
    UInt32 bytes = 0;
    const size_t header = offsetof(AudioBufferList, mBuffers);
    if (AudioObjectGetPropertyDataSize(device, &address, 0, NULL, &bytes) != noErr ||
        bytes < header || bytes > header + 256 * sizeof(AudioBuffer)) return 0;
    NSMutableData *storage = [NSMutableData dataWithLength:bytes];
    if (AudioObjectGetPropertyData(device, &address, 0, NULL, &bytes, storage.mutableBytes) != noErr || bytes < header) return 0;
    const AudioBufferList *streams = storage.bytes;
    if (streams->mNumberBuffers > (bytes - header) / sizeof(AudioBuffer)) return 0;
    NSUInteger channels = 0;
    for (UInt32 index = 0; index < streams->mNumberBuffers; index++) channels += streams->mBuffers[index].mNumberChannels;
    return channels <= 256 ? channels : 0;
}

static BOOL sameVolume(NSNumber *a, NSNumber *b) {
    return a && b && fabs(a.doubleValue - b.doubleValue) < .002;
}

static BOOL sameVolumes(NSArray<NSNumber *> *a, NSArray<NSNumber *> *b) {
    if (!a.count || a.count != b.count) return NO;
    for (NSUInteger index = 0; index < a.count; index++) {
        if (!sameVolume(a[index], b[index])) return NO;
    }
    return YES;
}

@interface JardCoreAudioOutput : NSObject <JardRecordingOutput>
@property(nonatomic) AudioObjectID device;
@property(nonatomic) AudioObjectPropertySelector selector;
@property(nonatomic, copy) NSArray<NSNumber *> *elements;
+ (instancetype)current;
@end

@implementation JardCoreAudioOutput

+ (instancetype)current {
    AudioObjectPropertyAddress defaultOutput = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain};
    AudioObjectID device = kAudioObjectUnknown;
    if (!readProperty(kAudioObjectSystemObject, defaultOutput, sizeof(device), &device) || device == kAudioObjectUnknown) return nil;

    AudioObjectPropertySelector selector = kAudioDevicePropertyVolumeScalar;
    NSMutableArray<NSNumber *> *channels = NSMutableArray.array;
    BOOL hasReadOnlyChannel = NO;
    NSUInteger count = outputChannels(device);
    for (AudioObjectPropertyElement element = 1; element <= count; element++) {
        AudioObjectPropertyAddress property = volumeAddress(selector, element);
        if (!AudioObjectHasProperty(device, &property)) continue;
        if (settableVolume(device, property)) [channels addObject:@(element)];
        else hasReadOnlyChannel = YES;
    }

    JardCoreAudioOutput *output = self.new;
    output.device = device;
    if (channels.count && !hasReadOnlyChannel) {
        output.selector = selector;
        output.elements = channels;
        return output;
    }
    if (hasReadOnlyChannel || !settableVolume(device, volumeAddress(selector, kAudioObjectPropertyElementMain))) {
        // The virtual main control preserves balance when we cannot safely set all channels.
        selector = virtualMainVolume;
        if (!settableVolume(device, volumeAddress(selector, kAudioObjectPropertyElementMain))) return nil;
    }
    output.selector = selector;
    output.elements = @[@(kAudioObjectPropertyElementMain)];
    return output;
}

- (NSArray<NSNumber *> *)readVolumes {
    NSMutableArray *volumes = NSMutableArray.array;
    for (NSNumber *element in self.elements) {
        NSNumber *volume = readVolume(self.device, volumeAddress(self.selector, element.unsignedIntValue));
        if (!volume) return nil;
        [volumes addObject:volume];
    }
    return volumes;
}

- (BOOL)writeVolumes:(NSArray<NSNumber *> *)volumes {
    NSArray *previous = [self readVolumes];
    if (!previous || previous.count != volumes.count) return NO;
    for (NSUInteger index = 0; index < self.elements.count; index++) {
        AudioObjectPropertyAddress address = volumeAddress(self.selector, self.elements[index].unsignedIntValue);
        if (writeVolume(self.device, address, [volumes[index] floatValue])) continue;
        // Roll back only writes we still own when a later channel refuses a change.
        for (NSUInteger written = 0; written < index; written++) {
            address = volumeAddress(self.selector, self.elements[written].unsignedIntValue);
            if (sameVolume(readVolume(self.device, address), volumes[written]))
                writeVolume(self.device, address, [previous[written] floatValue]);
        }
        return NO;
    }
    return YES;
}
@end

@interface JardRecordingEnvironment ()
@property(nonatomic, strong) dispatch_queue_t queue;
@property(nonatomic, copy) id<JardRecordingOutput> (^openOutput)(void);
@property(nonatomic, strong) id<JardRecordingOutput> output;
@property(nonatomic, strong) JardDictationMedia *media;
@property(nonatomic, strong) dispatch_source_t timer;
@property(nonatomic, copy) dispatch_block_t completion;
@property(nonatomic, copy) NSArray<NSNumber *> *original, *observed, *requested, *from, *target;
@property(nonatomic) NSTimeInterval rampStart, rampDuration;
@property(nonatomic) IOPMAssertionID sleepAssertion;
@property(nonatomic) BOOL active, closed, lower, pausingMedia;
@end

@implementation JardRecordingEnvironment

- (instancetype)init {
    return [self initWithOutput:^{ return [JardCoreAudioOutput current]; }];
}

- (instancetype)initWithOutput:(id<JardRecordingOutput> (^)(void))openOutput {
    if (!(self = [super init])) return nil;
    _queue = dispatch_queue_create("ai.jard.recording-environment", DISPATCH_QUEUE_SERIAL);
    _openOutput = [openOutput copy];
    return self;
}

- (void)releaseSleepAssertion {
    if (!self.sleepAssertion) return;
    IOPMAssertionRelease(self.sleepAssertion);
    self.sleepAssertion = 0;
}

- (BOOL)ownsVolumes:(NSArray<NSNumber *> *)volumes {
    return sameVolumes(volumes, self.observed) || sameVolumes(volumes, self.requested);
}

- (void)stopRamp {
    if (self.timer) dispatch_source_cancel(self.timer);
    self.timer = nil;
    self.from = self.target = nil;
}

- (void)clearOutput {
    [self stopRamp];
    self.output = nil;
    self.original = self.observed = self.requested = nil;
    dispatch_block_t completion = self.completion;
    self.completion = nil;
    if (completion) dispatch_async(dispatch_get_main_queue(), completion);
}

- (void)restoreImmediately {
    NSArray *current = [self.output readVolumes];
    if ([self ownsVolumes:current] && !sameVolumes(current, self.original))
        [self.output writeVolumes:self.original];
    [self clearOutput];
}

- (BOOL)writeStep:(NSArray<NSNumber *> *)volumes {
    if (![self.output writeVolumes:volumes]) return NO;
    self.requested = volumes;
    self.observed = [self.output readVolumes] ?: volumes;
    return YES;
}

- (void)tick {
    if (![self ownsVolumes:[self.output readVolumes]]) {
        [self clearOutput];
        return;
    }
    double elapsed = NSProcessInfo.processInfo.systemUptime - self.rampStart;
    double progress = fmin(1, elapsed / self.rampDuration);
    double eased = progress * progress * (3 - 2 * progress);
    NSMutableArray *step = NSMutableArray.array;
    for (NSUInteger index = 0; index < self.from.count; index++) {
        double from = self.from[index].doubleValue;
        [step addObject:@(from + (self.target[index].doubleValue - from) * eased)];
    }
    if (![self writeStep:step]) {
        [self restoreImmediately];
        return;
    }
    if (progress < 1 ||
        (!sameVolumes(self.observed, self.target) && elapsed < self.rampDuration + .5)) return;
    if (self.active) [self stopRamp];
    else [self clearOutput];
}

- (void)rampFrom:(NSArray<NSNumber *> *)from to:(NSArray<NSNumber *> *)target duration:(NSTimeInterval)duration {
    [self stopRamp];
    self.from = from;
    self.target = target;
    self.rampStart = NSProcessInfo.processInfo.systemUptime;
    self.rampDuration = duration;
    self.timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, self.queue);
    dispatch_source_set_timer(self.timer, dispatch_time(DISPATCH_TIME_NOW, 0), 16 * NSEC_PER_MSEC, 2 * NSEC_PER_MSEC);
    __weak typeof(self) owner = self;
    dispatch_source_set_event_handler(self.timer, ^{ [owner tick]; });
    dispatch_resume(self.timer);
}

- (void)beginWithMode:(NSString *)mode percent:(double)percent {
    dispatch_async(self.queue, ^{
        if (self.closed || self.active) return;
        [self restoreImmediately];
        self.active = YES;
        IOPMAssertionID assertion = 0;
        if (IOPMAssertionCreateWithName(kIOPMAssertionTypePreventUserIdleSystemSleep, kIOPMAssertionLevelOn,
                CFSTR("Jard dictation"), &assertion) == kIOReturnSuccess) self.sleepAssertion = assertion;

        self.pausingMedia = [mode isEqual:@"pause"];
        if (self.pausingMedia) {
            if (!self.media) self.media = JardDictationMedia.new;
            [self.media begin];
            return;
        }
        self.lower = [mode isEqual:@"lower"];
        if (!self.lower && ![mode isEqual:@"mute"]) return;
        self.output = self.openOutput();
        self.original = self.observed = self.requested = [self.output readVolumes];
        if (!self.original.count) { [self clearOutput]; return; }
        double factor = self.lower && isfinite(percent) ? fmax(0, fmin(100, percent)) / 100 : 0;
        NSMutableArray *target = NSMutableArray.array;
        for (NSNumber *volume in self.original) [target addObject:@(volume.doubleValue * factor)];
        if (sameVolumes(self.original, target)) { [self clearOutput]; return; }
        if (self.lower) [self rampFrom:self.original to:target duration:.12];
        else if (![self writeStep:target]) [self restoreImmediately];
    });
}

- (void)finish:(dispatch_block_t)completion {
    dispatch_async(self.queue, ^{
        if (!self.active) return;
        self.active = NO;
        [self releaseSleepAssertion];
        if (self.pausingMedia) [self.media finish];
        self.pausingMedia = NO;
        self.completion = completion;
        NSArray *current = [self.output readVolumes];
        if (self.lower && [self ownsVolumes:current] && !sameVolumes(current, self.original))
            [self rampFrom:current to:self.original duration:1];
        else [self restoreImmediately];
    });
}

- (void)shutdown {
    dispatch_sync(self.queue, ^{
        self.closed = YES;
        self.active = NO;
        [self releaseSleepAssertion];
        [self restoreImmediately];
        [self.media shutdown];
    });
}
@end
