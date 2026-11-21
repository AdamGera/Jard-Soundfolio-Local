#import "jard_calendar.h"
#import <EventKit/EventKit.h>

static NSString *const connectedKey = @"JardCalendarConnected";
static NSString *const remindersKey = @"JardCalendarReminders";
static NSString *const leadKey = @"JardCalendarLead";
static NSString *const exclusionsKey = @"JardCalendarExclusions";

@implementation JardCalendarMeeting
@end

static BOOL calendarHost(NSString *host, NSString *domain) {
    return [host isEqual:domain] || [host hasSuffix:[@"." stringByAppendingString:domain]];
}

static NSString *linkProvider(NSURL *url) {
    if (![url.scheme.lowercaseString isEqual:@"https"] || !url.host.length || url.user || url.password || url.port) return nil;
    NSString *host = url.host.lowercaseString, *path = url.path.lowercaseString;
    if ((calendarHost(host, @"zoom.us") || calendarHost(host, @"zoomgov.com") || calendarHost(host, @"zoom.com")) &&
        ([path hasPrefix:@"/j/"] || [path hasPrefix:@"/my/"] || [path hasPrefix:@"/w/"] || [path hasPrefix:@"/s/"]))
        return @"Zoom";
    if ([host isEqual:@"meet.google.com"] && JardMeetingBrowserCall(url, @"calendar")) return @"Google Meet";
    if ((calendarHost(host, @"teams.microsoft.com") || calendarHost(host, @"teams.cloud.microsoft") ||
        calendarHost(host, @"teams.live.com")) && ([path hasPrefix:@"/l/meetup-join/"] || [path hasPrefix:@"/meet/"]))
        return @"Microsoft Teams";
    if (calendarHost(host, @"slack.com") && [path containsString:@"/huddle"]) return @"Slack";
    if (calendarHost(host, @"webex.com") && ([path hasPrefix:@"/meet/"] || [path hasPrefix:@"/join/"] ||
        [path hasSuffix:@"/j.php"] || [path containsString:@"/wbxmjs/joinservice/"])) return @"Webex";
    return nil;
}

static NSURL *meetingLink(NSArray<NSString *> *fields, NSString **provider) {
    static NSRegularExpression *urls;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        urls = [NSRegularExpression regularExpressionWithPattern:@"https://[^\\s<>\\\"'()\\[\\]{}|\\\\]+"
            options:NSRegularExpressionCaseInsensitive error:nil];
    });
    NSCharacterSet *punctuation = [NSCharacterSet characterSetWithCharactersInString:@".,;:!?"];
    for (NSString *field in fields) {
        NSString *text = field.length > 16384 ? [field substringToIndex:16384] : field;
        __block NSURL *selected = nil;
        __block NSString *selectedProvider = nil;
        [urls enumerateMatchesInString:text options:0 range:NSMakeRange(0, text.length)
            usingBlock:^(NSTextCheckingResult *result, NSMatchingFlags flags, BOOL *stop) {
            if (!result) return;
            NSString *value = [[text substringWithRange:result.range] stringByTrimmingCharactersInSet:punctuation];
            NSURL *url = [NSURL URLWithString:value];
            NSString *label = linkProvider(url);
            if (label) {
                selected = url;
                selectedProvider = label;
                *stop = YES;
            }
        }];
        if (selected) {
            *provider = selectedProvider;
            return selected;
        }
    }
    return nil;
}

static NSString *calendarText(NSString *value, NSUInteger limit) {
    if (!value.length) return @"";
    NSString *text = [[value componentsSeparatedByCharactersInSet:NSCharacterSet.controlCharacterSet] componentsJoinedByString:@" "];
    text = [text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (text.length > limit) {
        NSRange last = [text rangeOfComposedCharacterSequenceAtIndex:limit - 1];
        text = [text substringToIndex:NSMaxRange(last) <= limit ? NSMaxRange(last) : last.location];
    }
    return text;
}

JardCalendarMeeting *JardCalendarOccurrence(NSString *key, NSString *title, NSString *calendar, NSDate *start, NSDate *end,
    BOOL excluded, NSArray<NSString *> *attendees, NSArray<NSString *> *fields) {
    if (excluded || !key.length || !start || !end || [end compare:start] != NSOrderedDescending) return nil;
    NSString *provider = nil;
    NSURL *link = meetingLink(fields, &provider);
    NSMutableOrderedSet<NSString *> *people = NSMutableOrderedSet.orderedSet;
    for (NSString *name in attendees) {
        NSString *trimmed = calendarText(name, 120);
        if (trimmed.length) [people addObject:trimmed];
        if (people.count >= 32) break;
    }
    if (!link && !people.count) return nil;
    JardCalendarMeeting *meeting = JardCalendarMeeting.new;
    meeting.identifier = [NSString stringWithFormat:@"%@|%.3f", key, start.timeIntervalSince1970];
    title = calendarText(title, 160);
    meeting.title = title.length ? title : @"Calendar meeting";
    meeting.calendar = calendar ?: @"";
    meeting.start = start;
    meeting.end = end;
    meeting.link = link;
    meeting.provider = provider;
    meeting.attendees = people.array;
    return meeting;
}

BOOL JardCalendarMatchesCall(JardCalendarMeeting *meeting, JardCall *call) {
    if (!meeting.provider || ![meeting.provider isEqual:call.label]) return NO;
    if (![meeting.provider isEqual:@"Google Meet"]) return YES;
    JardCall *scheduled = JardMeetingBrowserCall(meeting.link, call.application);
    return [scheduled.identity isEqual:call.identity];
}

@interface JardCalendar ()
@property(nonatomic, copy) void (^changed)(void);
@property(nonatomic, strong) dispatch_queue_t reader;
@property(nonatomic, strong) EKEventStore *store; // Accessed on reader.
@property(nonatomic, strong) id observer;          // Accessed on reader.
@property(nonatomic, copy) NSArray<JardCalendarMeeting *> *snapshot;
@property(nonatomic, copy) NSArray<NSDictionary *> *calendars;
@property(nonatomic, strong) NSMutableSet<NSString *> *exclusions;
@property(nonatomic, strong) NSDate *nextRead;
@property(nonatomic) BOOL requesting, grantedHere, deniedHere, connected, reminders, reading, closed;
@property(nonatomic) NSInteger lead;
@property(nonatomic) NSUInteger generation;
@end

@implementation JardCalendar

- (instancetype)initWithChanged:(void (^)(void))changed {
    if (!(self = [super init])) return nil;
    _changed = [changed copy];
    _reader = dispatch_queue_create("local.jard.calendar", DISPATCH_QUEUE_SERIAL);
    _snapshot = @[];
    _calendars = @[];
    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    _connected = [defaults boolForKey:connectedKey];
    _reminders = [defaults objectForKey:remindersKey] ? [defaults boolForKey:remindersKey] : YES;
    _lead = [defaults objectForKey:leadKey] ? [defaults integerForKey:leadKey] : 1;
    if (![@[@0, @1, @2, @5] containsObject:@(_lead)]) _lead = 1;
    _exclusions = [NSMutableSet setWithArray:[defaults stringArrayForKey:exclusionsKey] ?: @[]];
    return self;
}

- (NSString *)access {
    EKAuthorizationStatus status = [EKEventStore authorizationStatusForEntityType:EKEntityTypeEvent];
    // The first process can still see not determined after its completion block receives a grant.
    if (status != EKAuthorizationStatusNotDetermined) self.grantedHere = self.deniedHere = NO;
    if (status == EKAuthorizationStatusFullAccess || self.grantedHere) return @"granted";
    return status == EKAuthorizationStatusNotDetermined && !self.deniedHere ? @"notDetermined" : @"denied";
}

- (NSArray<JardCalendarMeeting *> *)meetings {
    if (!self.connected || ![[self access] isEqual:@"granted"]) return @[];
    return [self.snapshot filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(JardCalendarMeeting *event, NSDictionary *bindings) {
        return ![self.exclusions containsObject:event.calendar];
    }]];
}

- (NSDictionary *)statusAt:(NSDate *)now {
    NSString *access = [self access];
    BOOL visible = self.connected && [access isEqual:@"granted"];
    NSMutableArray *upcoming = NSMutableArray.array, *calendars = NSMutableArray.array;
    for (JardCalendarMeeting *event in self.meetings) {
        if ([event.end compare:now] != NSOrderedDescending) continue;
        [upcoming addObject:@{@"id": event.identifier, @"title": event.title, @"start": @(event.start.timeIntervalSince1970),
            @"end": @(event.end.timeIntervalSince1970), @"provider": event.provider ?: @"", @"canJoin": @(event.link != nil)}];
        if (upcoming.count == 3) break;
    }
    if (visible) for (NSDictionary *entry in self.calendars) {
        [calendars addObject:@{@"id": entry[@"id"], @"title": entry[@"title"], @"source": entry[@"source"],
            @"included": @(![self.exclusions containsObject:entry[@"id"]])}];
    }
    return @{@"access": access, @"connected": @(visible), @"reminders": @(self.reminders), @"lead": @(self.lead),
        @"upcoming": upcoming, @"calendars": calendars};
}

- (void)clearReader {
    dispatch_async(self.reader, ^{
        if (self.observer) [NSNotificationCenter.defaultCenter removeObserver:self.observer];
        self.observer = nil;
        self.store = nil;
    });
}

- (void)connect:(void (^)(NSString *))reply {
    if (self.closed || self.requesting) { reply(@"A calendar request is already in progress."); return; }
    self.requesting = YES;
    dispatch_async(self.reader, ^{
        if (!self.store) self.store = EKEventStore.new;
        [self.store requestFullAccessToEventsWithCompletion:^(BOOL granted, NSError *error) {
            if (granted) dispatch_async(self.reader, ^{ [self.store reset]; });
            dispatch_async(dispatch_get_main_queue(), ^{
                self.requesting = NO;
                if (self.closed) { reply(@"Jard is closing."); return; }
                self.grantedHere = granted;
                self.deniedHere = !granted;
                self.connected = granted;
                [NSUserDefaults.standardUserDefaults setBool:granted forKey:connectedKey];
                self.nextRead = nil;
                if (granted) [self refreshAt:NSDate.date];
                else [self clearReader];
                if (self.changed) self.changed();
                reply(nil);
            });
        }];
    });
}

- (void)disconnect {
    self.connected = NO;
    self.generation++;
    [NSUserDefaults.standardUserDefaults setBool:NO forKey:connectedKey];
    self.snapshot = @[];
    self.calendars = @[];
    self.nextRead = nil;
    [self clearReader];
    if (self.changed) self.changed();
}

- (void)setReminders:(BOOL)enabled {
    _reminders = enabled;
    [NSUserDefaults.standardUserDefaults setBool:enabled forKey:remindersKey];
    if (self.changed) self.changed();
}

- (void)setLead:(NSInteger)minutes {
    _lead = minutes;
    [NSUserDefaults.standardUserDefaults setInteger:minutes forKey:leadKey];
    if (self.changed) self.changed();
}

- (BOOL)setIncluded:(BOOL)included calendar:(NSString *)identifier {
    if (!self.connected || ![[self.calendars valueForKey:@"id"] containsObject:identifier]) return NO;
    if (included) [self.exclusions removeObject:identifier];
    else [self.exclusions addObject:identifier];
    [NSUserDefaults.standardUserDefaults setObject:self.exclusions.allObjects forKey:exclusionsKey];
    if (self.changed) self.changed();
    return YES;
}

- (void)refreshAt:(NSDate *)now {
    if (!self.connected || self.closed) return;
    if (![[self access] isEqual:@"granted"]) {
        if (self.snapshot.count || self.calendars.count || self.nextRead) {
            self.generation++;
            self.snapshot = @[];
            self.calendars = @[];
            self.nextRead = nil;
            [self clearReader];
            if (self.changed) self.changed();
        }
        return;
    }
    if (self.reading || (self.nextRead && [now compare:self.nextRead] == NSOrderedAscending &&
        [self.nextRead timeIntervalSinceDate:now] <= 60)) return;
    self.reading = YES;
    self.nextRead = [now dateByAddingTimeInterval:60];
    NSUInteger generation = self.generation;
    dispatch_async(self.reader, ^{
        @autoreleasepool {
            if (!self.store) self.store = EKEventStore.new;
            if (!self.observer) {
                __weak typeof(self) weak = self;
                self.observer = [NSNotificationCenter.defaultCenter addObserverForName:EKEventStoreChangedNotification
                    object:self.store queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *notification) {
                    if (!weak.nextRead) return;
                    weak.nextRead = [NSDate dateWithTimeIntervalSinceNow:1];
                }];
            }
            NSMutableArray *calendars = NSMutableArray.array, *meetings = NSMutableArray.array;
            for (EKCalendar *calendar in [self.store calendarsForEntityType:EKEntityTypeEvent]) {
                if (!calendar.calendarIdentifier.length) continue;
                [calendars addObject:@{@"id": calendar.calendarIdentifier, @"title": calendar.title ?: @"Calendar",
                    @"source": calendar.source.title ?: @""}];
            }
            NSPredicate *window = [self.store predicateForEventsWithStartDate:[now dateByAddingTimeInterval:-300]
                endDate:[now dateByAddingTimeInterval:48 * 3600] calendars:nil];
            NSArray<EKEvent *> *events = [[self.store eventsMatchingPredicate:window]
                sortedArrayUsingSelector:@selector(compareStartDateWithEvent:)];
            for (EKEvent *event in events) {
                if (event.allDay || event.status == EKEventStatusCanceled || event.availability == EKEventAvailabilityFree ||
                    [event.endDate compare:now] != NSOrderedDescending) continue;
                NSMutableArray *names = NSMutableArray.array;
                BOOL declined = NO;
                for (EKParticipant *person in event.attendees) {
                    if (person.isCurrentUser) { declined |= person.participantStatus == EKParticipantStatusDeclined; continue; }
                    if (person.participantType == EKParticipantTypeRoom || person.participantType == EKParticipantTypeResource) continue;
                    if (person.name.length && names.count < 32) [names addObject:person.name];
                    else if (person.URL.resourceSpecifier.length && names.count < 32) [names addObject:person.URL.resourceSpecifier];
                }
                JardCalendarMeeting *meeting = JardCalendarOccurrence(event.eventIdentifier ?: event.calendarItemIdentifier,
                    event.title, event.calendar.calendarIdentifier, event.startDate, event.endDate, declined, names,
                    @[event.URL.absoluteString ?: @"", event.location ?: @"", event.notes ?: @""]);
                if (meeting) [meetings addObject:meeting];
                if (meetings.count >= 200) break;
            }
            dispatch_async(dispatch_get_main_queue(), ^{
                self.reading = NO;
                if (self.closed || generation != self.generation || !self.connected || ![[self access] isEqual:@"granted"]) return;
                self.calendars = calendars;
                self.snapshot = meetings;
                if (self.changed) self.changed();
            });
        }
    });
}

- (void)shutdown {
    self.closed = YES;
    self.generation++;
    [self clearReader];
}
@end
